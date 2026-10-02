import { EventEmitter } from "node:events";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 进程管理器测试。
 *
 * `spawn` 被整个替换掉：这些用例要验的是**状态机与判定**
 * （什么时候接管、什么时候不去起、什么时候把进程树收掉），
 * 真起进程既慢又不可重复。
 */

const spawnMock = vi.hoisted(() => vi.fn());

vi.mock("node:child_process", () => ({
  spawn: spawnMock,
}));

import {
  BACKEND_PORT,
  buildServiceCommand,
  FRONTEND_PORT,
  parsePidRecords,
  ServiceManager,
} from "../src/main/serviceManager";
import { derivePackagedPaths, deriveServicePaths, type ServicePaths } from "../src/main/servicePaths";
import { DEFAULT_SERVICE_SETTINGS } from "../src/shared/ipc";

/** 假子进程：只会发出 spawn / error，永远不自己退 */
interface FakeChild extends EventEmitter {
  pid: number;
  stdout: PassThrough;
  stderr: PassThrough;
  kill: () => boolean;
}

function createFakeChild(pid: number, options: { failWith?: string } = {}): FakeChild {
  const child = new EventEmitter() as FakeChild;

  child.pid = pid;
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kill = () => true;

  queueMicrotask(() => {
    if (options.failWith) {
      child.emit("error", new Error(options.failWith));
    } else {
      child.emit("spawn");
    }
  });

  return child;
}

let projectRoot: string;
let logDir: string;
let managers: ServiceManager[] = [];

/** 造一个结构完整（但内容为空）的假项目目录：`describeMissingPath` 只看文件在不在 */
beforeEach(() => {
  spawnMock.mockReset();
  managers = [];

  projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "hypra-project-"));
  logDir = fs.mkdtempSync(path.join(os.tmpdir(), "hypra-logs-"));

  fs.mkdirSync(path.join(projectRoot, ".venv", "Scripts"), { recursive: true });
  fs.writeFileSync(path.join(projectRoot, ".venv", "Scripts", "python.exe"), "");
  fs.mkdirSync(path.join(projectRoot, "_local", "qdrant"), { recursive: true });
  fs.writeFileSync(path.join(projectRoot, "_local", "qdrant", "qdrant.exe"), "");
  fs.mkdirSync(path.join(projectRoot, "backend"), { recursive: true });
  // 前端的判据是「目录 + 它自己的依赖」，两样都要在（见 describeMissingFrontendPath）
  fs.mkdirSync(path.join(projectRoot, "frontend", "node_modules"), { recursive: true });
});

afterEach(() => {
  for (const manager of managers) {
    manager.dispose();
  }

  fs.rmSync(projectRoot, { recursive: true, force: true });
  fs.rmSync(logDir, { recursive: true, force: true });
});

function createManager(options: {
  probe?: (url: string, timeoutMs: number) => Promise<boolean>;
  kill?: (pid: number) => Promise<void>;
  /** undefined = 用假项目目录；null = 模拟「找不到项目根」 */
  paths?: ServicePaths | null;
} = {}): { manager: ServiceManager; probe: ReturnType<typeof vi.fn>; kill: ReturnType<typeof vi.fn> } {
  const probe = vi.fn(options.probe ?? (async () => false));
  const kill = vi.fn(options.kill ?? (async () => undefined));
  const paths = options.paths === undefined ? deriveServicePaths(projectRoot) : options.paths;

  const manager = new ServiceManager({
    resolvePaths: () => paths,
    logDir,
    readSettings: async () => DEFAULT_SERVICE_SETTINGS,
    probe,
    kill,
  });

  managers.push(manager);

  return { manager, probe, kill };
}

describe("buildServiceCommand", () => {
  const paths = deriveServicePaths("/repo");

  it("Qdrant 直接跑可执行文件，cwd 落在它所在目录（storage 跟着它走）", () => {
    expect(buildServiceCommand("qdrant", paths, { backendReload: false })).toEqual({
      command: paths.qdrantExe,
      args: [],
      cwd: paths.qdrantDir,
    });
  });

  it("后端走 uvicorn，cwd 在 backend（app 包在那里），默认不带 --reload", () => {
    const command = buildServiceCommand("backend", paths, { backendReload: false });

    expect(command.command).toBe(paths.pythonExe);
    expect(command.cwd).toBe(paths.backendDir);
    expect(command.args).toEqual([
      "-m",
      "uvicorn",
      "app.main:app",
      "--port",
      String(BACKEND_PORT),
    ]);
  });

  it("只有开了开关才追加 --reload", () => {
    expect(buildServiceCommand("backend", paths, { backendReload: true }).args).toContain("--reload");
  });

  it("打包形态直接跑 backend.exe：不拼 uvicorn 参数，也忽略 --reload", () => {
    const packaged = derivePackagedPaths({
      resourcesPath: "/install/resources",
      userDataDir: "/users/demo/HyPRA",
    });
    const command = buildServiceCommand("backend", packaged, { backendReload: true });

    expect(command.command).toBe(packaged.pythonExe);
    expect(command.args).toEqual([]);
    expect(command.cwd).toBe(packaged.backendDir);
  });

  it("前端走 npm run dev，cwd 落在 frontend，端口显式钉住", () => {
    // 钉端口不是多此一举：默认端口被占时 Next dev 会自己往后找（3001、3002…），
    // 而健康检查只探 3000——不钉的话那种情况会表现成「进程活着但一直不就绪」
    const npmArgs = ["run", "dev", "--", "--port", String(FRONTEND_PORT)];
    const command = buildServiceCommand("frontend", paths, { backendReload: false });

    expect(command.cwd).toBe(paths.frontendDir);

    if (process.platform === "win32") {
      // npm 在 Windows 上是 npm.cmd，CreateProcess 不能直接执行 .cmd，必须过一层 cmd.exe
      expect(command).toEqual({
        command: "cmd.exe",
        args: ["/c", "npm", ...npmArgs],
        cwd: paths.frontendDir,
      });
    } else {
      expect(command).toEqual({ command: "npm", args: npmArgs, cwd: paths.frontendDir });
    }
  });

  it("前端的启动命令与打包形态无关（发布包不含 Web 端，走不到这里）", () => {
    const packaged = derivePackagedPaths({
      resourcesPath: "/install/resources",
      userDataDir: "/users/demo/HyPRA",
    });

    expect(buildServiceCommand("frontend", packaged, { backendReload: false }).cwd).toBe(
      packaged.frontendDir,
    );
  });
});

describe("parsePidRecords", () => {
  it("解析合法记录", () => {
    const raw = JSON.stringify({ qdrant: { pid: 10, image: "qdrant.exe" } });

    expect(parsePidRecords(raw)).toEqual([{ id: "qdrant", pid: 10, image: "qdrant.exe" }]);
  });

  it("坏 JSON / 非对象 / 非法 PID / 未知服务一律当空（不能因此崩掉启动）", () => {
    expect(parsePidRecords("{")).toEqual([]);
    expect(parsePidRecords("null")).toEqual([]);
    expect(parsePidRecords(JSON.stringify({ qdrant: { pid: -1 } }))).toEqual([]);
    expect(parsePidRecords(JSON.stringify({ qdrant: { pid: "10" } }))).toEqual([]);
    expect(parsePidRecords(JSON.stringify({ unknown: { pid: 3 } }))).toEqual([]);
  });
});

describe("ServiceManager", () => {
  it("端口上已有同类服务时接管显示：不重复拉起、也不去停别人的进程", async () => {
    const { manager, kill } = createManager({ probe: async () => true });

    const states = await manager.startService("qdrant");
    const qdrant = states.find((state) => state.id === "qdrant");

    expect(qdrant?.phase).toBe("adopted");
    expect(qdrant?.pid).toBeNull();
    expect(spawnMock).not.toHaveBeenCalled();

    await manager.stopService("qdrant");
    expect(kill).not.toHaveBeenCalled();
  });

  it("找不到项目根时报可读原因，不硬起进程", async () => {
    const { manager } = createManager({ paths: null });

    const states = await manager.startService("qdrant");
    const qdrant = states.find((state) => state.id === "qdrant");

    expect(qdrant?.phase).toBe("failed");
    expect(qdrant?.detail).toContain("找不到项目根");
    expect(spawnMock).not.toHaveBeenCalled();
  });

  it("健康检查通过后进入 ready 并记下 PID", async () => {
    spawnMock.mockImplementation(() => createFakeChild(4242));

    // 第一次探测是「端口已被占用？」，之后是就绪轮询
    let probes = 0;
    const { manager } = createManager({
      probe: async () => {
        probes += 1;

        return probes > 1;
      },
    });

    const states = await manager.startService("qdrant");
    const qdrant = states.find((state) => state.id === "qdrant");

    expect(qdrant?.phase).toBe("ready");
    expect(qdrant?.pid).toBe(4242);
    expect(spawnMock).toHaveBeenCalledTimes(1);
  });

  it("停止时按 PID 收掉整个进程树（Windows 上靠 taskkill /T 才带得上孙进程）", async () => {
    spawnMock.mockImplementation(() => createFakeChild(777));

    let probes = 0;
    const { manager, kill } = createManager({
      probe: async () => {
        probes += 1;

        return probes > 1;
      },
    });

    await manager.startService("qdrant");
    await manager.stopService("qdrant");

    expect(kill).toHaveBeenCalledWith(777);
    expect(manager.getStates().find((state) => state.id === "qdrant")?.phase).toBe("stopped");
  });

  it("spawn 失败（如路径不存在）报 failed 并带上底层原因", async () => {
    spawnMock.mockImplementation(() => createFakeChild(0, { failWith: "spawn ENOENT" }));

    const { manager } = createManager();
    const states = await manager.startService("backend");
    const backend = states.find((state) => state.id === "backend");

    expect(backend?.phase).toBe("failed");
    expect(backend?.detail).toContain("ENOENT");
  });

  it("Qdrant 没就绪就不去起后端（省得留下一个连不上记忆库的半死后端）", async () => {
    const { manager } = createManager({ paths: null });

    const states = await manager.startAll();

    expect(states.find((state) => state.id === "qdrant")?.phase).toBe("failed");
    expect(states.find((state) => state.id === "backend")?.phase).toBe("failed");
    expect(states.find((state) => state.id === "backend")?.detail).toContain("Qdrant 未就绪");
  });

  it("stopAll 收掉自己起的进程，但不碰被外部接管的那一个", async () => {
    spawnMock.mockImplementation(() => createFakeChild(1000));

    let backendProbes = 0;
    const probe = async (url: string): Promise<boolean> => {
      if (url.includes("6333")) {
        return true; // Qdrant 外部已在跑 → 接管
      }

      backendProbes += 1;

      return backendProbes > 1; // 后端的接管探测 false，就绪探测 true
    };

    const { manager, kill } = createManager({ probe });

    await manager.startAll();

    expect(manager.getStates().find((state) => state.id === "qdrant")?.phase).toBe("adopted");
    expect(manager.getStates().find((state) => state.id === "backend")?.phase).toBe("ready");

    await manager.stopAll();

    expect(kill).toHaveBeenCalledTimes(1);
    expect(kill).toHaveBeenCalledWith(1000);
    // 外部接管的那个保持 adopted：它的进程仍在跑，状态不能谎报成「未启动」
    expect(manager.getStates().find((state) => state.id === "qdrant")?.phase).toBe("adopted");
    expect(manager.getStates().find((state) => state.id === "backend")?.phase).toBe("stopped");
  });

  it("已就绪时再点启动是无操作（不会起出第二个）", async () => {
    spawnMock.mockImplementation(() => createFakeChild(55));

    let probes = 0;
    const { manager } = createManager({
      probe: async () => {
        probes += 1;

        return probes > 1;
      },
    });

    await manager.startService("qdrant");
    await manager.startService("qdrant");

    expect(spawnMock).toHaveBeenCalledTimes(1);
  });

  it("startAll 只拉向量库与后端：前端按需启动，不跟着程序一起起来", async () => {
    // 全探通 → 前两个走「接管」路径，连 spawn 都不需要，便于看清谁被碰过
    const { manager, probe } = createManager({ probe: async () => true });

    const states = await manager.startAll();

    expect(states.find((state) => state.id === "frontend")?.phase).toBe("stopped");
    // 关键：连探测都不该探它——探了就说明走了启动流程（程序一开就白搭一个 Node 服务）
    expect(probe.mock.calls.some((call) => String(call[0]).includes("3000"))).toBe(false);
    expect(spawnMock).not.toHaveBeenCalled();
  });

  it("前端缺目录时报的是前端自己的原因，不拿 Python / Qdrant 那套判据说事", async () => {
    // 假项目目录里后端那几样都在，唯独没有 frontend
    fs.rmSync(path.join(projectRoot, "frontend"), { recursive: true, force: true });

    const { manager } = createManager();
    const states = await manager.startService("frontend");
    const frontend = states.find((state) => state.id === "frontend");

    expect(frontend?.phase).toBe("failed");
    expect(frontend?.detail).toContain("找不到 Web 前端目录");
    expect(frontend?.detail).not.toContain("Python");
    expect(spawnMock).not.toHaveBeenCalled();
  });

  it("端口 3000 上已有前端在跑时接管显示：不重复拉起，也不去停别人的进程", async () => {
    const { manager, kill } = createManager({ probe: async (url) => url.includes("3000") });

    const states = await manager.startService("frontend");
    const frontend = states.find((state) => state.id === "frontend");

    expect(frontend?.phase).toBe("adopted");
    expect(spawnMock).not.toHaveBeenCalled();

    await manager.stopService("frontend");
    expect(kill).not.toHaveBeenCalled();
  });

  it("状态变化会推给订阅者（界面不用轮询）", async () => {
    spawnMock.mockImplementation(() => createFakeChild(9));

    const seen: string[][] = [];
    const manager = new ServiceManager({
      resolvePaths: () => deriveServicePaths(projectRoot),
      logDir,
      readSettings: async () => DEFAULT_SERVICE_SETTINGS,
      probe: (() => {
        let probes = 0;

        return async () => {
          probes += 1;

          return probes > 1;
        };
      })(),
      kill: async () => undefined,
      onChanged: (states) => {
        seen.push(states.map((state) => `${state.id}:${state.phase}`));
      },
    });

    managers.push(manager);

    await manager.startService("qdrant");

    expect(seen.length).toBeGreaterThan(0);
    expect(seen.at(-1)).toContain("qdrant:ready");
  });
});
