import { app, globalShortcut } from "electron";
import path from "node:path";

import { startAssetServer, type AssetServer } from "./assetServer";
import { publishServicesChanged, registerConsoleIpc } from "./consoleIpc";
import {
  configureConsoleWindow,
  destroyConsoleWindow,
  showConsoleWindow,
  toggleConsoleWindow,
} from "./consoleWindow";
import { commitSettings, registerIpc, restoreSettings } from "./ipc";
import { petModelRoot } from "./petSettings";
import {
  configurePetWindow,
  destroyPetWindow,
  getPetSettings,
  getPetWindowState,
  onPetWindowStateChanged,
  togglePetWindow,
} from "./petWindow";
import { createAppTray, nextScale, type AppTray } from "./tray";
import { ensureUserEnvFile } from "./serviceEnv";
import {
  cleanupStaleProcesses,
  createServiceManager,
  type ServiceManager,
} from "./serviceManager";
import { fileExists, resolveServicePaths } from "./servicePaths";
import { loadServiceSettings } from "./serviceSettings";

/**
 * HyPRA 桌面端主进程入口。
 *
 * 两个窗口，两种定位：
 * - **程序控制台**（`console.html`）：有边框的常规窗口，是「配置与调试主入口」，
 *   启动后默认显示它；
 * - **桌宠窗**（`pet.html`）：无边框透明窗，不自动显示，由用户在控制台里选「桌面模式」后启动。
 *
 * 另有托盘常驻，以及为渲染层提供的一个 loopback 静态资源服务。
 *
 * 对话 / 记忆 / 情绪 / 数字人驱动全部由后端 FastAPI 提供——
 * 本进程不接触任何模型 API Key，也不代理业务请求。
 */
app.setName("HyPRA");

/** 后端地址：构建期可用 HYPRA_API_BASE 覆盖（后端跑在别的机器时用） */
const API_BASE = process.env.HYPRA_API_BASE ?? "http://localhost:8000";

/** 开发模式：直接加载 Vite dev server（HMR）；生产走本地静态资源服务 */
const DEV_SERVER_URL = process.env.HYPRA_DEV_SERVER_URL;

let assetServer: AssetServer | null = null;
let tray: AppTray | null = null;
/** 受管的本机服务（Qdrant / 后端 / Web 前端）：关掉本程序时一起收掉 */
let services: ServiceManager | null = null;

/** 退出收尾的整体上限：卡住也要退出去，不能被一个不听话的子进程拖死 */
const SHUTDOWN_TIMEOUT_MS = 8_000;

/**
 * 服务路径的候选起始目录（开发形态用）。
 *
 * 开发态从 `desktop/dist/main` 往上就是项目根；打包态不走这里
 * （那时程序自带 backend / qdrant，见 `packagedServicePaths()`）。
 */
function serviceSearchRoots(): string[] {
  return [
    path.join(__dirname, "..", "..", ".."), // desktop/dist/main → 项目根
    path.join(__dirname, "..", ".."), // desktop/dist/main → desktop
    app.getAppPath(),
    path.dirname(app.getPath("exe")),
    process.cwd(),
  ];
}

/**
 * 打包形态的资源/用户目录；开发形态返回 null。
 *
 * `app.isPackaged` 是 Electron 的权威判据，不能靠路径特征猜：开发态
 * `process.resourcesPath` 指向 Electron 自己的 resources，拿它去找
 * backend 只会找到空气。
 */
function packagedServicePaths(): { resourcesPath: string; userDataDir: string } | null {
  if (!app.isPackaged) {
    return null;
  }

  return {
    resourcesPath: process.resourcesPath,
    userDataDir: app.getPath("userData"),
  };
}

/** 状态变化后同步托盘菜单（勾选项、提示文字） */
function refreshTray(): void {
  const state = getPetWindowState();

  tray?.refresh({
    visible: state.visible,
    clickThrough: state.clickThrough,
    alwaysOnTop: state.alwaysOnTop,
    scale: state.scale,
  });
}

/** IPC 层依赖：提供后端地址与环境信息，并在设置生效后刷新托盘 */
const ipcDependencies = {
  apiBase: API_BASE,
  development: Boolean(DEV_SERVER_URL),
  onSettingsApplied: refreshTray,
};

/** 单实例锁：重复启动时唤出已有桌宠，而不是放出第二只 */
const gotLock = app.requestSingleInstanceLock();

if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    // 重复启动时唤出控制台（程序主入口），而不是放出第二只桌宠
    void showConsoleWindow();
  });

  /*
   * 退出收尾必须是**异步**的：要等服务进程真的被收掉（taskkill 有 5s 上限）。
   * `before-quit` 本身不支持 await，所以先 preventDefault 按住它，
   * 收完再 `app.exit(0)`（exit 不会再触发 before-quit，不会绕回来）。
   */
  let quitting = false;

  const shutdown = async (): Promise<void> => {
    globalShortcut.unregisterAll();
    tray?.destroy();
    tray = null;
    destroyPetWindow();
    destroyConsoleWindow();

    const cleanup = async (): Promise<void> => {
      await assetServer?.close();
      assetServer = null;
      await services?.stopAll();
      services?.dispose();
      services = null;
    };

    await Promise.race([
      cleanup(),
      new Promise<void>((resolve) => {
        setTimeout(() => resolve(), SHUTDOWN_TIMEOUT_MS);
      }),
    ]);

    app.exit(0);
  };

  app.on("before-quit", (event) => {
    if (quitting) {
      return;
    }

    quitting = true;
    event.preventDefault();
    void shutdown();
  });

  app.on("window-all-closed", () => {
    // 两个窗的关闭都被拦成 hide，正常使用不会走到这里（退出只走托盘 / 控制台）
    app.quit();
  });

  app
    .whenReady()
    .then(async () => {
      // ① 生产模式先起静态资源服务：前端资源引用是绝对路径（/live2d、/portraits），
      //    只有同源的 HTTP 服务才能让复用层零改动
      if (!DEV_SERVER_URL) {
        assetServer = await startAssetServer({
          root: path.join(__dirname, "../renderer"),
          backendOrigin: API_BASE,
          // 用户自备的 Live2D 模型：发布包不带模型，从用户数据目录映射 /live2d
          extraRoots: { "/live2d": petModelRoot() },
        });
      }

      configurePetWindow({ origin: DEV_SERVER_URL ?? assetServer?.origin ?? "" });
      configureConsoleWindow({
        origin: DEV_SERVER_URL ?? assetServer?.origin ?? "",
        devServerUrl: DEV_SERVER_URL,
      });

      // ② 建托盘 → 恢复设置 → 订阅状态变化，保证菜单一上来就是正确的勾选状态
      tray = createAppTray(
        {
          openConsole: () => {
            void toggleConsoleWindow();
          },
          toggleVisibility: () => {
            void togglePetWindow();
          },
          toggleClickThrough: () => {
            commitSettings({ clickThrough: !getPetSettings().clickThrough }, ipcDependencies);
          },
          toggleAlwaysOnTop: () => {
            commitSettings({ alwaysOnTop: !getPetSettings().alwaysOnTop }, ipcDependencies);
          },
          changeScale: (direction) => {
            commitSettings(
              { scale: nextScale(getPetSettings().scale, direction) },
              ipcDependencies,
            );
          },
        },
        { ...getPetWindowState() },
      );

      await restoreSettings(ipcDependencies);
      onPetWindowStateChanged(refreshTray);
      registerIpc(ipcDependencies);

      // ③ 本机服务（Qdrant / 后端；Web 前端按需启动）：先清掉上次被强杀留下的孤儿，
      //    再按设置决定要不要自动拉起
      const serviceLogDir = path.join(app.getPath("userData"), "logs");
      const serviceSettings = await loadServiceSettings();
      const packaged = packagedServicePaths();

      // 打包形态首次启动：把 .env 模板送到用户数据目录，用户改一处即可填 key
      //（已存在则不动，见 serviceEnv.ts）
      if (packaged) {
        await ensureUserEnvFile(packaged);
      }

      await cleanupStaleProcesses(serviceLogDir);

      services = createServiceManager({
        resolvePaths: (settings) =>
          resolveServicePaths({
            searchFrom: serviceSearchRoots(),
            overrides: {
              projectRoot: settings.projectRoot,
              pythonPath: settings.pythonPath,
              qdrantPath: settings.qdrantPath,
            },
            exists: fileExists,
            packaged,
          }),
        logDir: serviceLogDir,
        readSettings: loadServiceSettings,
        onChanged: publishServicesChanged,
      });

      registerConsoleIpc({
        apiBase: API_BASE,
        development: Boolean(DEV_SERVER_URL),
        onStateApplied: refreshTray,
        // 复用桌宠窗那套提交逻辑（校验 + 落盘 + 广播 + 刷新托盘）
        applyPetSettings: (patch) => commitSettings(patch, ipcDependencies),
        services: {
          getStates: () => services?.getStates() ?? [],
          startService: (id) => services?.startService(id) ?? Promise.resolve([]),
          stopService: (id) => services?.stopService(id) ?? Promise.resolve([]),
          applySettings: (settings) => services?.applySettings(settings),
        },
      });

      if (serviceSettings.autoStart) {
        // 不 await：窗口先出来，启动进度由控制台的「本机服务」实时显示
        void services.startAll();
      }

      // ④ 全局快捷键：Ctrl+Alt+P 显示 / 隐藏桌宠（注册失败不阻断启动）
      const registered = globalShortcut.register("Control+Alt+P", () => {
        void togglePetWindow();
      });

      if (!registered) {
        console.warn("[pet] 全局快捷键 Ctrl+Alt+P 注册失败（可能已被其它程序占用）");
      }

      // ⑤ 启动后显示控制台（程序主入口）：桌宠与 Web 端都由用户在里面选定后再启动
      await showConsoleWindow();
    })
    .catch((error: unknown) => {
      console.error("[pet] 桌面端启动失败", error);
      app.quit();
    });
}
