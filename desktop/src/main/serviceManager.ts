import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import type { WriteStream } from "node:fs";
import path from "node:path";

import type {
  ServiceId,
  ServicePhase,
  ServiceSettings,
  ServiceState,
  ServiceStateListener,
} from "../shared/ipc";
import {
  describeMissingFrontendPath,
  describeMissingPath,
  fileExists,
  type ServicePaths,
} from "./servicePaths";

/**
 * 受管服务的进程管理。
 *
 * 职责：按顺序拉起 Qdrant、后端与 Web 前端，等它们通过健康检查、在桌面端退出时
 * **连同孙进程**一起收尾。
 *
 * 但「受管」不等于「随程序自动启动」：`startAll` 只拉 Qdrant 与后端，
 * **前端按需启动**（用户点了「本机服务 → 启动」或「以 Web 模式启动」才拉），
 * 理由见 `startAll` 的注释。
 *
 * 本模块**刻意不 import electron**：日志目录与设置读取都由调用方注入，
 * 因此它可以在 vitest 里用假的 `child_process` 完整测一遍状态机。
 */

/** 受管服务清单（顺序即启动顺序：向量库 → 后端 → 前端，后者依赖前者） */
export const SERVICE_IDS: readonly ServiceId[] = ["qdrant", "backend", "frontend"];

/** 后端端口（与 `backend/app/config.py` 默认值、docker-compose 一致） */
export const BACKEND_PORT = 8000;
/** Qdrant REST 端口（其默认值） */
export const QDRANT_PORT = 6333;
/**
 * Web 前端端口（Next dev 的默认值）。
 *
 * ⚠️ 它同时被 `DEFAULT_CONSOLE_SETTINGS.webUrl` 与 `start.ps1` 的 `$FrontendPort`
 * 引用着——改这里就要把那两处一起改，否则「以 Web 模式启动」会去开一个
 * 没人监听的地址。
 */
export const FRONTEND_PORT = 3000;

/**
 * 健康检查地址：这就是「就绪」的判据。
 *
 * 用 `127.0.0.1` 而非 `localhost`：Windows 上 `localhost` 可能先解析到 IPv6，
 * 而两个服务都只监听 IPv4，探测会因为族不匹配而假失败。
 */
export const SERVICE_HEALTH_URLS: Record<ServiceId, string> = {
  qdrant: `http://127.0.0.1:${QDRANT_PORT}/healthz`,
  backend: `http://127.0.0.1:${BACKEND_PORT}/health`,
  /*
   * Next dev 没有专门的健康端点，探首页即可：返回 200 就说明服务在监听且能出页。
   * 顺带把首页编译掉——用户点开浏览器时不会先看一段白屏。
   */
  frontend: `http://127.0.0.1:${FRONTEND_PORT}/`,
};

/**
 * 就绪等待上限。
 *
 * 后端比 Qdrant 慢得多：启动时要读预设 / 世界书 / 连记忆库，冷启动几十秒正常，
 * 不能按 Qdrant 的尺度去判它失败。前端更慢一档：Next dev 首次要把整个应用
 * 编译一遍（缓存过之后只要几秒），按后端的 60s 判它会误报。
 */
export const SERVICE_READY_TIMEOUT_MS: Record<ServiceId, number> = {
  qdrant: 20_000,
  backend: 60_000,
  frontend: 120_000,
};

/** 停止单个进程的等待上限：超时也必须继续退出，不能把关闭流程卡死 */
export const KILL_TIMEOUT_MS = 5_000;

/** 健康轮询间隔 */
const HEALTH_POLL_INTERVAL_MS = 500;
/** 单次健康探测的超时 */
const HEALTH_PROBE_TIMEOUT_MS = 2_000;
/** 「端口上已有服务」的探测超时：要快，否则每次启动都被拖慢 */
const ADOPT_PROBE_TIMEOUT_MS = 1_500;

/** 一条启动命令（拆出来便于测试，不必真起进程） */
export interface ServiceCommand {
  command: string;
  args: string[];
  cwd: string;
}

/** 构造某个服务的启动命令（纯函数） */
export function buildServiceCommand(
  id: ServiceId,
  paths: ServicePaths,
  options: { backendReload: boolean },
): ServiceCommand {
  if (id === "qdrant") {
    // cwd 设为可执行文件所在目录：Qdrant 把 storage / snapshots 落在 cwd 下
    return { command: paths.qdrantExe, args: [], cwd: paths.qdrantDir };
  }

  if (id === "frontend") {
    /*
     * 前端走包管理器的 dev 脚本（与 `start.ps1` 的 `npm run dev` 同一口径）。
     *
     * Windows 上 npm 实际是 `npm.cmd`，而 `CreateProcess` 不能直接执行 .cmd，
     * 必须过一层 cmd.exe。这里**不用 `shell: true`**：显式写 cmd.exe 更好读，
     * 而且进程映像名就是 `cmd.exe`，与 PID 记录里存的名字对得上——
     * 上次被强杀留下的孤儿靠核对映像名才敢下手（见 `cleanupStaleProcesses`）。
     * 参数全是固定字面量，没有拼接任何用户输入，因此没有注入面。
     *
     * 其它平台 npm 本身就是可执行文件，直接起。
     *
     * 端口显式钉成 `FRONTEND_PORT`：默认端口被占时 Next dev 会自己往后找
     * （3001、3002…），而健康检查只探 3000——不钉端口的话，那种情况会表现成
     * 「进程活着但一直不就绪」，查起来比直接失败难得多。
     */
    if (process.platform === "win32") {
      return {
        command: "cmd.exe",
        args: ["/c", "npm", "run", "dev", "--", "--port", String(FRONTEND_PORT)],
        cwd: paths.frontendDir,
      };
    }

    return {
      command: "npm",
      args: ["run", "dev", "--", "--port", String(FRONTEND_PORT)],
      cwd: paths.frontendDir,
    };
  }

  if (paths.packaged) {
    // 打包形态：backend.exe 自带运行时，自己就会起 uvicorn
    //（见 backend/run_backend.py），所以不拼 `-m uvicorn`。
    // `--reload` 在打包产物上没有意义（它靠监视源码目录工作），直接忽略。
    //
    // ⚠️ 端口靠两边默认值一致：这里不传参，后端取自己的默认 8000
    //（`run_backend.py` 的 HYPRA_PORT 默认值），与 BACKEND_PORT 必须同步改。
    return { command: paths.pythonExe, args: [], cwd: paths.backendDir };
  }

  const args = ["-m", "uvicorn", "app.main:app", "--port", String(BACKEND_PORT)];

  if (options.backendReload) {
    // --reload 会多出 reloader + worker 两级子进程，收尾更依赖进程树清理
    args.push("--reload");
  }

  return { command: paths.pythonExe, args, cwd: paths.backendDir };
}

/** 健康探测签名（注入以便测试） */
export type ServiceProbe = (url: string, timeoutMs: number) => Promise<boolean>;

/** 默认健康探测：任一声明成功即算健康 */
async function defaultProbe(url: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, { signal: controller.signal });

    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 跑一个辅助命令（tasklist / taskkill）并等它结束；超时就放弃，绝不阻塞调用方 */
function runToCompletion(command: string, args: string[], timeoutMs: number): Promise<string> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { windowsHide: true, stdio: ["ignore", "pipe", "ignore"] });
    let output = "";
    let settled = false;

    const finish = (): void => {
      if (settled) {
        return;
      }

      settled = true;
      clearTimeout(timer);
      resolve(output);
    };

    const timer = setTimeout(() => {
      child.kill();
      finish();
    }, timeoutMs);

    child.stdout?.on("data", (chunk: Buffer) => {
      output += chunk.toString("utf8");
    });
    child.once("error", finish);
    child.once("exit", finish);
  });
}

/**
 * 杀掉整个进程树。
 *
 * Windows 上 `child.kill()` 只杀直接子进程，`uvicorn --reload` 的 worker 会留下来变孤儿，
 * 所以必须用 `taskkill /T`（连带子孙）；其它平台用 SIGTERM 让进程自己收尾。
 */
export async function killProcessTree(pid: number, timeoutMs = KILL_TIMEOUT_MS): Promise<void> {
  if (process.platform === "win32") {
    await runToCompletion("taskkill", ["/PID", String(pid), "/T", "/F"], timeoutMs);

    return;
  }

  try {
    process.kill(pid, "SIGTERM");
  } catch {
    // 进程可能已经自己退了，这不算失败
  }
}

/** 读取某 PID 的进程映像名（仅 Windows 可确认；其它平台返回 null = 无法确认） */
async function readImageName(pid: number): Promise<string | null> {
  if (process.platform !== "win32") {
    return null;
  }

  const output = await runToCompletion(
    "tasklist",
    ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"],
    KILL_TIMEOUT_MS,
  );
  // 输出形如："qdrant.exe","1234","Console","1","85,000 K"
  const match = /^"([^"]+)"/m.exec(output);

  return match ? match[1].toLowerCase() : null;
}

/** PID 记录：记下映像名，供下次启动判断「这个 PID 还是不是我们上次起的那个」 */
export interface PidRecord {
  id: ServiceId;
  pid: number;
  image: string;
}

/** 解析 PID 记录文件（坏内容一律当空，不抛错） */
export function parsePidRecords(raw: string): PidRecord[] {
  let parsed: unknown;

  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }

  if (!parsed || typeof parsed !== "object") {
    return [];
  }

  const source = parsed as Record<string, unknown>;
  const records: PidRecord[] = [];

  for (const id of SERVICE_IDS) {
    const entry = source[id];

    if (!entry || typeof entry !== "object") {
      continue;
    }

    const { pid, image } = entry as { pid?: unknown; image?: unknown };

    if (typeof pid !== "number" || !Number.isInteger(pid) || pid <= 0) {
      continue;
    }

    records.push({ id, pid, image: typeof image === "string" ? image : "" });
  }

  return records;
}

/** PID 记录文件位置（与日志同目录） */
export function servicePidFilePath(logDir: string): string {
  return path.join(logDir, "services.json");
}

/**
 * 清理上次残留。
 *
 * 场景：上次桌面端被任务管理器强杀，`before-quit` 没跑到，子进程成了孤儿。
 * 这里按记录把它们收掉——但**先核对进程映像名**：PID 会被系统回收，
 * 隔几天再启动时那个号很可能已经属于别的程序，不能照着旧记录盲杀。
 */
export async function cleanupStaleProcesses(
  logDir: string,
  kill: (pid: number) => Promise<void> = killProcessTree,
): Promise<ServiceId[]> {
  const file = servicePidFilePath(logDir);
  let raw: string;

  try {
    raw = await fs.promises.readFile(file, "utf8");
  } catch {
    return [];
  }

  const killed: ServiceId[] = [];

  for (const record of parsePidRecords(raw)) {
    const image = await readImageName(record.pid);

    /*
     * 只在「要么确认不了、要么确认下来与记录一致」时才下手：
     * 能确认映像名却不一致（PID 被系统回收给了别的程序），或者记录里根本没存映像名
     * （无从核对），都一律跳过。宁可不杀，也不能误杀。
     */
    const matches =
      image !== null &&
      record.image !== "" &&
      image === path.basename(record.image).toLowerCase();

    if (image !== null && !matches) {
      continue;
    }

    await kill(record.pid);
    killed.push(record.id);
  }

  try {
    await fs.promises.rm(file, { force: true });
  } catch {
    // 删不掉也无所谓：下次还会按同样的规则核对一遍
  }

  return killed;
}

/** 一个受管服务的运行时状态 */
interface Runtime {
  phase: ServicePhase;
  pid: number | null;
  detail: string;
  child: ChildProcess | null;
  logStream: WriteStream | null;
  /** 停止流程已发起：此时进程退出不再算「崩溃」 */
  cancelled: boolean;
  /**
   * 进程内服务的停止函数（打包形态的 Web 端）。
   *
   * 与 `child` 互斥：走 `spawn` 的服务不填它，进程内的服务不填 `child`。
   * 停止时按「哪个有值」二选一，因此两条路径不会互相干扰。
   */
  stopInternal: (() => Promise<void>) | null;
}

export interface ServiceManagerOptions {
  /**
   * 由设置解析路径。
   *
   * 做成回调而不是构造时定死：用户可能中途改项目根，改完要能重新解析。
   * 返回 null 表示找不到项目（界面会显示可读的失败原因）。
   */
  resolvePaths: (settings: ServiceSettings) => ServicePaths | null;
  /** 日志与 PID 记录目录（主进程传 `userData/logs`；模块本身不依赖 electron） */
  logDir: string;
  /** 读取当前服务设置 */
  readSettings: () => Promise<ServiceSettings>;
  /** 健康探测（测试可注入） */
  probe?: ServiceProbe;
  /** 杀进程树（测试可注入，避免测试真去 taskkill） */
  kill?: (pid: number) => Promise<void>;
  /** 状态变化回调 */
  onChanged?: ServiceStateListener;
  /**
   * **进程内**服务启动器。
   *
   * 打包形态下 Web 端不是一个子进程，而是主进程里跑的一个 HTTP 服务
   * （复用 `assetServer` 托管随包静态产物）。它没有 PID 可杀、也没有命令行，
   * 因此无法走 `spawnService` 那条路 —— 由这个回调接管启停。
   *
   * 返回一个停止函数（`stopAll` / `stopService` 时调用）。
   * 开发形态不传它，`frontend` 保持 `npm run dev` 子进程的老路径。
   */
  startInternalService?: (id: ServiceId, paths: ServicePaths) => Promise<{
    /** 停止该服务并释放端口 */
    stop: () => Promise<void>;
    /** 供界面展示的地址（如 `http://127.0.0.1:3000`） */
    url: string;
  }>;
}

export class ServiceManager {
  private readonly runtimes = new Map<ServiceId, Runtime>();
  private readonly probe: ServiceProbe;
  private readonly kill: (pid: number) => Promise<void>;
  private paths: ServicePaths | null = null;
  private disposed = false;

  constructor(private readonly options: ServiceManagerOptions) {
    for (const id of SERVICE_IDS) {
      this.runtimes.set(id, {
        phase: "stopped",
        pid: null,
        detail: "未启动",
        child: null,
        logStream: null,
        cancelled: false,
        stopInternal: null,
      });
    }

    this.probe = options.probe ?? defaultProbe;
    this.kill = options.kill ?? killProcessTree;
  }

  /** 当前状态快照 */
  getStates(): ServiceState[] {
    return SERVICE_IDS.map((id) => {
      const runtime = this.mustGet(id);

      return {
        id,
        phase: runtime.phase,
        pid: runtime.pid,
        detail: runtime.detail,
        healthUrl: SERVICE_HEALTH_URLS[id],
      };
    });
  }

  /** 设置变化后重新解析路径（下次启动生效） */
  applySettings(settings: ServiceSettings): void {
    this.paths = this.options.resolvePaths(settings);
  }

  /**
   * 按依赖顺序启动：Qdrant 就绪后再起后端。
   *
   * **前端刻意不在这里**。它是「按需启动」的服务：程序一开就拉一个 Next dev
   * 服务，会让只想用桌宠的人白搭一份内存与一个端口，也违背「桌宠窗与 Web 端
   * 都不会自动打开，由用户在控制台里选定」的设计。前端由两条路径按需拉起：
   * 控制台「本机服务 → 启动」，以及「以 Web 模式启动」（见 `consoleIpc.ts`）。
   *
   * 也因此 `SERVICE_IDS` 与这里不是一回事：前者是「受管清单」（决定状态列表、
   * 退出收尾），后者只是「随程序自动拉起的那些」。
   */
  async startAll(): Promise<ServiceState[]> {
    await this.startService("qdrant");

    const qdrant = this.mustGet("qdrant");

    if (qdrant.phase !== "ready" && qdrant.phase !== "adopted") {
      // 后端启动时会连向量库，上游没起来就先别起——省得留下一个半死的后端
      this.setPhase("backend", "failed", `Qdrant 未就绪，后端未启动（${qdrant.detail}）`, null);

      return this.getStates();
    }

    await this.startService("backend");

    return this.getStates();
  }

  /** 停止全部（顺序与启动相反：先停后端，再停向量库） */
  async stopAll(): Promise<void> {
    for (const id of [...SERVICE_IDS].reverse()) {
      const runtime = this.mustGet(id);

      if (runtime.phase === "adopted") {
        // 别人的进程不动它，也**不改它的状态**：「外部运行中」是事实，
        // 改成「未启动」会让界面撒谎——服务还在跑，用户却以为可以重新启动，
        // 点下去就会起出第二个实例（端口冲突）
        continue;
      }

      runtime.cancelled = true;

      // 进程内服务先走自己的停止函数，再清空引用（重复调 stopAll 不会重复停）
      if (runtime.stopInternal) {
        const stop = runtime.stopInternal;
        runtime.stopInternal = null;

        try {
          await stop();
        } catch {
          // 收尾阶段不因单个服务停不掉而中断整体退出
        }
      }

      await this.killRuntime(id);
      runtime.phase = "stopped";
      runtime.detail = "未启动";
    }

    await this.writePidFile();
    this.emit();
  }

  /** 启动单个服务；已是 ready / starting / adopted 时是无操作 */
  async startService(id: ServiceId): Promise<ServiceState[]> {
    const runtime = this.mustGet(id);

    if (runtime.phase === "starting" || runtime.phase === "ready" || runtime.phase === "adopted") {
      return this.getStates();
    }

    const settings = await this.options.readSettings();
    this.paths = this.options.resolvePaths(settings);

    if (!this.paths) {
      this.setPhase(
        id,
        "failed",
        "找不到项目根（候选目录里没有同时含 .venv 与 _local/qdrant 的目录），请在服务设置里指定项目根",
      );

      return this.getStates();
    }

    // 前置检查按服务分岔：前端要的是 Node 项目与它自己的依赖，
    // 与 Python / Qdrant 无关——拿后端那套判据去查它，报出来的错会指错方向。
    const missing =
      id === "frontend"
        ? describeMissingFrontendPath(this.paths, fileExists)
        : describeMissingPath(this.paths, fileExists);

    if (missing) {
      this.setPhase(id, "failed", missing);

      return this.getStates();
    }

    // 坑①：端口上已经有同类服务 → 接管显示，既不重复拉起，也绝不停止它
    if (await this.probe(SERVICE_HEALTH_URLS[id], ADOPT_PROBE_TIMEOUT_MS)) {
      this.setPhase(id, "adopted", "已在运行（不是桌面端启动的，桌面端不会停止它）", null);

      return this.getStates();
    }

    // 打包形态的 Web 端：不是一个可 spawn 的进程，而是主进程内的 HTTP 服务。
    // 单独一条分支，尽早分流——它没有 PID、没有命令行、也没有日志文件。
    if (id === "frontend" && this.paths.packaged && this.options.startInternalService) {
      return this.startInternalFrontend(runtime);
    }

    const command = buildServiceCommand(id, this.paths, {
      backendReload: settings.backendReload,
    });

    runtime.cancelled = false;
    this.setPhase(id, "starting", "启动中…", null);

    const child = this.spawnService(id, command);
    const spawned = await this.waitSpawn(child);

    if (!spawned.ok) {
      runtime.child = null;
      runtime.pid = null;
      this.closeLog(id);
      this.setPhase(id, "failed", `启动失败：${spawned.error ?? "未知原因"}`);

      return this.getStates();
    }

    runtime.child = child;
    runtime.pid = child.pid ?? null;
    this.setPhase(id, "starting", "等待就绪…", runtime.pid);
    await this.writePidFile();

    const ready = await this.waitReady(id);

    if (runtime.cancelled) {
      // 等待期间被 stop 掉了，状态由 stop 那一路负责
      return this.getStates();
    }

    if (ready) {
      this.setPhase(id, "ready", `运行中（PID ${runtime.pid ?? "?"}）`, runtime.pid);
    } else if (this.mustGet(id).phase === "starting") {
      // 重新取一次当前状态：等待期间可能被 stopService 改掉，而 TS 的控制流
      // 分析看不到跨 await 的并发修改（直接读 runtime.phase 会被收窄成恒假分支）
      await this.killRuntime(id);
      this.setPhase(
        id,
        "failed",
        `启动超时（${Math.round(SERVICE_READY_TIMEOUT_MS[id] / 1000)}s 内未通过健康检查），日志见 ${this.logFilePath(id)}`,
      );
    }

    await this.writePidFile();

    return this.getStates();
  }

  /**
   * 启动**进程内** Web 端（打包形态）。
   *
   * 与 `spawn` 路径的差别：
   *
   * - 没有子进程，因此不写 PID 记录、不开日志文件（服务就在本进程里，
   *   异常会走主进程日志）；
   * - 「就绪」不以退出码判断，而是启动成功即视为就绪——`assetServer` 在
   *   端口绑定完成后才 resolve，此时服务已可访问；
   * - 停止时调 `stopInternal`，不是杀进程树。
   *
   * 健康探测仍保留一次：万一端口被别人占了，`assetServer` 会自行递增端口，
   * 这里不需要额外处理，但**先探一次**能避免与外部服务抢同一个端口。
   */
  private async startInternalFrontend(runtime: Runtime): Promise<ServiceState[]> {
    const paths = this.paths;

    if (!paths || !this.options.startInternalService) {
      this.setPhase("frontend", "failed", "进程内 Web 端启动器不可用");

      return this.getStates();
    }

    this.setPhase("frontend", "starting", "启动中…", null);

    try {
      const handle = await this.options.startInternalService("frontend", paths);

      // 启动是异步的，期间可能已被 stop：那就立刻回滚，别留一个野服务
      if (runtime.cancelled) {
        await handle.stop();
        runtime.stopInternal = null;
        runtime.phase = "stopped";
        runtime.detail = "未启动";
        this.emit();

        return this.getStates();
      }

      runtime.stopInternal = handle.stop;
      runtime.child = null;
      runtime.pid = null;
      this.setPhase("frontend", "ready", `运行中（随包静态服务 ${handle.url}）`, null);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);

      this.setPhase("frontend", "failed", `启动失败：${message}`);
    }

    return this.getStates();
  }

  /** 停止单个服务；被外部接管的不停（会重新探一次，避免界面停在过期状态） */
  async stopService(id: ServiceId): Promise<ServiceState[]> {
    const runtime = this.mustGet(id);

    if (runtime.phase === "adopted") {
      if (await this.probe(SERVICE_HEALTH_URLS[id], ADOPT_PROBE_TIMEOUT_MS)) {
        this.setPhase(id, "adopted", "已在运行（不是桌面端启动的，桌面端不会停止它）", null);
      } else {
        this.setPhase(id, "stopped", "未启动", null);
      }

      return this.getStates();
    }

    // 进程内服务（打包形态的 Web 端）：没有 child / pid，靠 stopInternal 收尾
    if (runtime.stopInternal) {
      runtime.cancelled = true;

      const stop = runtime.stopInternal;
      runtime.stopInternal = null;

      try {
        await stop();
      } catch {
        // 停不掉也要把状态置为 stopped：端口若真没释放，下次启动会自行递增端口
      }

      this.setPhase(id, "stopped", "未启动", null);
      await this.writePidFile();

      return this.getStates();
    }

    if (!runtime.child && runtime.pid === null) {
      this.setPhase(id, "stopped", "未启动", null);

      return this.getStates();
    }

    runtime.cancelled = true;
    await this.killRuntime(id);
    this.setPhase(id, "stopped", "未启动", null);
    await this.writePidFile();

    return this.getStates();
  }

  /** 关闭日志流（桌面端退出前调用，避免句柄悬着） */
  dispose(): void {
    this.disposed = true;

    for (const id of SERVICE_IDS) {
      this.closeLog(id);
    }
  }

  // ---------- 内部 ----------

  private mustGet(id: ServiceId): Runtime {
    const runtime = this.runtimes.get(id);

    if (!runtime) {
      throw new Error(`未知的受管服务：${id}`);
    }

    return runtime;
  }

  private setPhase(id: ServiceId, phase: ServicePhase, detail: string, pid?: number | null): void {
    const runtime = this.mustGet(id);

    runtime.phase = phase;
    runtime.detail = detail;

    if (pid !== undefined) {
      runtime.pid = pid;
    }

    this.emit();
  }

  private emit(): void {
    if (this.disposed) {
      return;
    }

    this.options.onChanged?.(this.getStates());
  }

  /**
   * 起进程。
   *
   * 只给 `cwd`，**不给也不读 `env`**：子进程沿用父进程环境，而本模块从不查看、
   * 过滤或记录任何环境变量——模型 API Key 一律由后端自己读 `backend/.env`。
   * 这是项目硬约束（AGENTS.md：主进程不接触 API Key）。
   */
  private spawnService(id: ServiceId, command: ServiceCommand): ChildProcess {
    const child = spawn(command.command, command.args, {
      cwd: command.cwd,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const stream = this.logStream(id);

    child.stdout?.pipe(stream, { end: false });
    child.stderr?.pipe(stream, { end: false });

    child.once("exit", (code, signal) => {
      const runtime = this.mustGet(id);

      if (runtime.child !== child) {
        return; // 已经被 stop 处理过了
      }

      runtime.child = null;
      runtime.pid = null;
      this.closeLog(id);

      if (runtime.cancelled) {
        return;
      }

      const reason = signal ? `signal=${signal}` : `code=${code ?? "?"}`;
      this.setPhase(id, "failed", `进程意外退出（${reason}），日志见 ${this.logFilePath(id)}`, null);
      void this.writePidFile();
    });

    return child;
  }

  /** 等 `spawn` 或 `error`：ENOENT 这类错误是异步事件，try/catch 包 spawn 抓不到 */
  private waitSpawn(child: ChildProcess): Promise<{ ok: boolean; error?: string }> {
    return new Promise((resolve) => {
      let settled = false;

      const finish = (result: { ok: boolean; error?: string }): void => {
        if (settled) {
          return;
        }

        settled = true;
        resolve(result);
      };

      child.once("spawn", () => finish({ ok: true }));
      child.once("error", (error: Error) => finish({ ok: false, error: error.message }));
    });
  }

  /** 轮询健康检查直到通过或超时 */
  private async waitReady(id: ServiceId): Promise<boolean> {
    const deadline = Date.now() + SERVICE_READY_TIMEOUT_MS[id];
    const url = SERVICE_HEALTH_URLS[id];

    while (Date.now() < deadline) {
      const runtime = this.mustGet(id);

      if (runtime.cancelled || runtime.phase === "failed") {
        return false; // 进程已经没了，不必再等到超时
      }

      if (await this.probe(url, HEALTH_PROBE_TIMEOUT_MS)) {
        return true;
      }

      await delay(HEALTH_POLL_INTERVAL_MS);
    }

    return false;
  }

  private async killRuntime(id: ServiceId): Promise<void> {
    const runtime = this.mustGet(id);
    const pid = runtime.pid ?? runtime.child?.pid ?? null;

    runtime.child = null;
    runtime.pid = null;

    if (pid !== null) {
      await this.kill(pid);
    }

    this.closeLog(id);
  }

  private logFilePath(id: ServiceId): string {
    return path.join(this.options.logDir, `${id}.log`);
  }

  /** 日志流按服务懒创建（追加写：多次启停的日志连着看） */
  private logStream(id: ServiceId): WriteStream {
    const runtime = this.mustGet(id);

    if (runtime.logStream) {
      return runtime.logStream;
    }

    fs.mkdirSync(this.options.logDir, { recursive: true });

    const stream = fs.createWriteStream(this.logFilePath(id), { flags: "a" });

    // 日志写失败（磁盘满 / 被占用）不该把桌面端带崩
    stream.on("error", () => undefined);
    runtime.logStream = stream;

    return stream;
  }

  private closeLog(id: ServiceId): void {
    const runtime = this.mustGet(id);

    runtime.logStream?.end();
    runtime.logStream = null;
  }

  /** 把在管的 PID 落盘：下次启动据此清理上次被强杀留下的孤儿 */
  private async writePidFile(): Promise<void> {
    const entries: Record<string, { pid: number; image: string }> = {};

    for (const id of SERVICE_IDS) {
      const runtime = this.mustGet(id);

      if (runtime.pid !== null && runtime.phase !== "adopted") {
        const command = this.paths
          ? buildServiceCommand(id, this.paths, { backendReload: false }).command
          : "";

        entries[id] = { pid: runtime.pid, image: command ? path.basename(command) : "" };
      }
    }

    try {
      await fs.promises.mkdir(this.options.logDir, { recursive: true });
      await fs.promises.writeFile(
        servicePidFilePath(this.options.logDir),
        `${JSON.stringify(entries, null, 2)}\n`,
        "utf8",
      );
    } catch {
      // 落盘失败不影响运行：最坏情况是下次没有孤儿可清
    }
  }
}

/** 方便主进程按依赖注入地创建（与其它模块的用法一致） */
export function createServiceManager(options: ServiceManagerOptions): ServiceManager {
  return new ServiceManager(options);
}
