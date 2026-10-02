import { BrowserWindow, app, ipcMain, shell } from "electron";

import {
  CONSOLE_CHANNELS,
  isLocalFrontendUrl,
  normalizeConsoleSettings,
  normalizeServiceSettings,
  type ConsoleState,
  type OpenWebResult,
  type PetEnvironment,
  type PetSettings,
  type ServiceId,
  type ServiceSettings,
  type ServiceState,
} from "../shared/ipc";
import { loadConsoleSettings, saveConsoleSettings } from "./consoleSettings";
import {
  requireConsoleSettingsPatch,
  requireServiceId,
  requireServiceSettingsPatch,
  requireSettingsPatch,
} from "./ipcValidation";
import { getPetSettings, getPetWindowState, hidePetWindow, showPetWindow } from "./petWindow";
import { FRONTEND_PORT } from "./serviceManager";
import { loadServiceSettings, saveServiceSettings } from "./serviceSettings";

/**
 * 控制台窗的 IPC。
 *
 * 与桌宠窗的 IPC 分开一个文件：两者的能力面完全不同（桌宠是拖拽/穿透/缩放，
 * 控制台是「启动哪个模式 + 配置 + 本机服务」），混在一个 registerIpc 里
 * 会让人以为它们共享权限。
 */

/**
 * 受管服务的端口。
 *
 * 由宿主注入（而不是这里直接 import 管理器）：管理器需要日志目录与设置读取，
 * 那些只有 `index.ts` 知道；IPC 层只管把请求转过去。
 */
export interface ManagedServicesPort {
  getStates(): ServiceState[];
  startService(id: ServiceId): Promise<ServiceState[]>;
  stopService(id: ServiceId): Promise<ServiceState[]>;
  /** 设置变化后同步给管理器（路径 / reload 下次启动生效） */
  applySettings(settings: ServiceSettings): void;
}

export interface ConsoleIpcDependencies {
  /** 后端 API 基址（与桌宠窗共用同一个值） */
  apiBase: string;
  /** 是否为开发模式 */
  development: boolean;
  /** 状态变化后刷新托盘菜单（桌宠显隐会影响菜单文案） */
  onStateApplied(): void;
  /**
   * 应用桌宠设置。
   *
   * 由宿主注入（而不是这里直接 import `ipc.ts`）：
   * “校验 + 落盘 + 广播 + 刷新托盘”那一整套只应该有一份实现，
   * 控制台与桌宠窗改的是同一份设置，不能被两条通路各写一遍。
   */
  applyPetSettings(patch: Partial<PetSettings>): PetSettings;
  /** 受管服务（Qdrant / 后端 / Web 前端） */
  services: ManagedServicesPort;
}

/** 向所有窗口广播（控制台与桌宠窗都可能订阅） */
function broadcast(channel: string, payload: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send(channel, payload);
    }
  }
}

/**
 * 服务状态变化后推给界面。
 *
 * 由宿主在管理器的 `onChanged` 回调里调用：状态变化大多发生在 IPC 调用之外
 * （启动就绪、进程意外退出），那时没有请求可回复，只能主动推。
 */
export function publishServicesChanged(states: ServiceState[]): void {
  broadcast(CONSOLE_CHANNELS.servicesChanged, states);
}

export function registerConsoleIpc(deps: ConsoleIpcDependencies): void {
  const environment = (): PetEnvironment => ({
    apiBase: deps.apiBase,
    appVersion: app.getVersion(),
    platform: process.platform,
    development: deps.development,
  });

  const snapshot = async (): Promise<ConsoleState> => ({
    petVisible: getPetWindowState().visible,
    petState: getPetWindowState(),
    petSettings: getPetSettings(),
    settings: await loadConsoleSettings(),
    environment: environment(),
    services: deps.services.getStates(),
    serviceSettings: await loadServiceSettings(),
  });

  const publishState = async (): Promise<ConsoleState> => {
    const state = await snapshot();

    broadcast(CONSOLE_CHANNELS.stateChanged, state);
    deps.onStateApplied();

    return state;
  };

  ipcMain.handle(CONSOLE_CHANNELS.getState, () => snapshot());
  ipcMain.handle(CONSOLE_CHANNELS.getSettings, () => loadConsoleSettings());

  ipcMain.handle(CONSOLE_CHANNELS.updateSettings, async (_event, patch: unknown) => {
    const current = await loadConsoleSettings();
    const next = normalizeConsoleSettings({ ...current, ...requireConsoleSettingsPatch(patch) });

    await saveConsoleSettings(next);
    broadcast(CONSOLE_CHANNELS.settingsChanged, next);
    await publishState();

    return next;
  });

  // 「以桌面模式启动」：显示桌宠窗（进程已经起来了，这里只是让它现身）
  ipcMain.handle(CONSOLE_CHANNELS.showPet, async () => {
    await showPetWindow();

    return publishState();
  });

  ipcMain.handle(CONSOLE_CHANNELS.hidePet, async () => {
    hidePetWindow();

    return publishState();
  });

  // 桌宠设置：走与桌宠窗同一条实现（commitSettings），因此两边改的必然是同一份
  ipcMain.handle(CONSOLE_CHANNELS.updatePetSettings, async (_event, patch: unknown) => {
    deps.applyPetSettings(requireSettingsPatch(patch));

    return publishState();
  });

  /*
   * 「以 Web 模式启动」：需要时先把本机前端拉起来，再交给系统浏览器。
   *
   * 为什么要在这里管前端：浏览器打开的是 Next 服务，而它平时并不在跑——
   * 只开浏览器的话，用户看到的是「无法访问此网站」。所以当地址指向**本机前端
   * 默认端口**时，先把服务拉到就绪再开；指向远程地址或别的端口时不管
   * （那种前端不归这台机器管，本地起一个只会白占端口）。
   *
   * 起不来就**不开浏览器**：开过去也只是一张错误页，不如把原因直接说清楚。
   */
  ipcMain.handle(CONSOLE_CHANNELS.openWeb, async (): Promise<OpenWebResult> => {
    const settings = await loadConsoleSettings();

    if (!settings.webUrl) {
      return { ok: false, reason: "还没配置 Web 端地址" };
    }

    if (isLocalFrontendUrl(settings.webUrl, FRONTEND_PORT)) {
      const states = await deps.services.startService("frontend");
      const frontend = states.find((state) => state.id === "frontend");

      if (frontend?.phase !== "ready" && frontend?.phase !== "adopted") {
        return {
          ok: false,
          reason: `Web 前端未能就绪：${frontend?.detail ?? "服务管理不可用"}`,
        };
      }
    }

    try {
      await shell.openExternal(settings.webUrl);

      return { ok: true };
    } catch (error: unknown) {
      return {
        ok: false,
        reason: error instanceof Error ? error.message : "打开浏览器失败",
      };
    }
  });

  ipcMain.handle(CONSOLE_CHANNELS.startService, async (_event, id: unknown) => {
    await deps.services.startService(requireServiceId(id));

    return publishState();
  });

  ipcMain.handle(CONSOLE_CHANNELS.stopService, async (_event, id: unknown) => {
    await deps.services.stopService(requireServiceId(id));

    return publishState();
  });

  ipcMain.handle(CONSOLE_CHANNELS.updateServiceSettings, async (_event, patch: unknown) => {
    const current = await loadServiceSettings();
    const next = normalizeServiceSettings({ ...current, ...requireServiceSettingsPatch(patch) });

    await saveServiceSettings(next);
    deps.services.applySettings(next);
    broadcast(CONSOLE_CHANNELS.servicesChanged, deps.services.getStates());
    await publishState();

    return next;
  });

  ipcMain.on(CONSOLE_CHANNELS.quit, () => {
    // app.quit 会走 before-quit 的清理流程（销毁窗口、停掉本机服务、关闭静态资源服务）
    app.quit();
  });
}
