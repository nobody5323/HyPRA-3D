import { BrowserWindow, app, ipcMain, shell } from "electron";

import {
  CONSOLE_CHANNELS,
  normalizeConsoleSettings,
  type ConsoleState,
  type OpenWebResult,
  type PetEnvironment,
  type PetSettings,
} from "../shared/ipc";
import { loadConsoleSettings, saveConsoleSettings } from "./consoleSettings";
import { requireConsoleSettingsPatch, requireSettingsPatch } from "./ipcValidation";
import { getPetSettings, getPetWindowState, hidePetWindow, showPetWindow } from "./petWindow";

/**
 * 控制台窗的 IPC。
 *
 * 与桌宠窗的 IPC 分开一个文件：两者的能力面完全不同（桌宠是拖拽/穿透/缩放，
 * 控制台是「启动哪个模式 + 配置」），混在一个 registerIpc 里会让人以为它们共享权限。
 */

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
}

/** 向所有窗口广播（控制台与桌宠窗都可能订阅） */
function broadcast(channel: string, payload: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send(channel, payload);
    }
  }
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
   * 「以 Web 模式启动」：交给系统浏览器。
   *
   * 不在这里替用户起前端服务——桌面端与 Web 端是两种部署形态（Web 端要 Next 服务），
   * 桌面端擅自拉起一个 node 进程只会带来端口冲突与孤儿进程。打不开就把原因返回给界面。
   */
  ipcMain.handle(CONSOLE_CHANNELS.openWeb, async (): Promise<OpenWebResult> => {
    const settings = await loadConsoleSettings();

    if (!settings.webUrl) {
      return { ok: false, reason: "还没配置 Web 端地址" };
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

  ipcMain.on(CONSOLE_CHANNELS.quit, () => {
    // app.quit 会走 before-quit 的清理流程（销毁窗口、关闭静态资源服务）
    app.quit();
  });
}
