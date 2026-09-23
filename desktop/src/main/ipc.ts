import { BrowserWindow, app, ipcMain } from "electron";

import { PET_CHANNELS, normalizePetSettings, type PetSettings } from "../shared/ipc";
import { requireBoolean, requirePoint, requireSettingsPatch } from "./ipcValidation";
import {
  applyPetSettings,
  endPetWindowDrag,
  getPetSettings,
  getPetWindowState,
  hidePetWindow,
  movePetWindowDrag,
  setPetWindowClickThrough,
  setPetWindowControlInteractive,
  startPetWindowDrag,
} from "./petWindow";
import { loadPetSettings, savePetSettings } from "./petSettings";

export interface IpcEnvironment {
  /** 后端 API 基址（构建期由 HYPRA_API_BASE 注入） */
  apiBase: string;
  /** 是否为开发模式 */
  development: boolean;
}

export interface IpcDependencies extends IpcEnvironment {
  /** 设置落盘并生效后通知托盘刷新勾选状态 */
  onSettingsApplied(settings: PetSettings): void;
}

/** 向所有窗口广播（当前只有桌宠窗，保持通用） */
function broadcast(channel: string, payload: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) {
      window.webContents.send(channel, payload);
    }
  }
}

/**
 * 注册「单向消息」通道的通用包装。
 *
 * `ipcMain.on` 的回调里抛异常会变成主进程的未捕获错误；这里统一吞掉并记日志，
 * 非法入参只让这一次调用失效，不影响桌宠继续运行。
 */
function handleValidated(channel: string, handler: (payload: unknown) => void): void {
  ipcMain.on(channel, (_event, payload: unknown) => {
    try {
      handler(payload);
    } catch (error: unknown) {
      console.warn(`[pet] 忽略非法的 IPC 调用 ${channel}`, error);
    }
  });
}

/** 应用设置并广播（渲染层与托盘都跟着更新） */
export function commitSettings(patch: Partial<PetSettings>, deps: IpcDependencies): PetSettings {
  const next = normalizePetSettings({ ...getPetSettings(), ...patch });
  const state = applyPetSettings(next);

  void savePetSettings(next).catch((error: unknown) => {
    console.warn("[pet] 保存设置失败", error);
  });

  broadcast(PET_CHANNELS.settingsChanged, next);
  broadcast(PET_CHANNELS.stateChanged, state);
  deps.onSettingsApplied(next);

  return next;
}

/**
 * 注册桌宠窗的 IPC 处理。
 *
 * 渲染层只能通过 preload 暴露的 `window.hyprPet` 走到这里，
 * 每个入口都会再校验一次入参类型。
 */
export function registerIpc(deps: IpcDependencies): void {
  ipcMain.handle(PET_CHANNELS.getState, () => getPetWindowState());
  ipcMain.handle(PET_CHANNELS.getSettings, () => getPetSettings());
  ipcMain.handle(PET_CHANNELS.getEnvironment, () => ({
    apiBase: deps.apiBase,
    appVersion: app.getVersion(),
    platform: process.platform,
    development: deps.development,
  }));

  ipcMain.handle(PET_CHANNELS.setClickThrough, (_event, value: unknown) =>
    commitSettings({ clickThrough: requireBoolean(value, "value") }, deps),
  );

  ipcMain.handle(PET_CHANNELS.setControlInteractive, (_event, value: unknown) =>
    setPetWindowControlInteractive(requireBoolean(value, "value")),
  );

  ipcMain.handle(PET_CHANNELS.updateSettings, (_event, patch: unknown) => {
    const settings = commitSettings(requireSettingsPatch(patch), deps);

    return settings;
  });

  handleValidated(PET_CHANNELS.startDrag, (payload) => {
    startPetWindowDrag(requirePoint(payload));
  });

  handleValidated(PET_CHANNELS.moveDrag, (payload) => {
    movePetWindowDrag(requirePoint(payload));
  });

  handleValidated(PET_CHANNELS.endDrag, () => {
    void endPetWindowDrag();
  });

  handleValidated(PET_CHANNELS.hide, () => {
    hidePetWindow();
  });

  handleValidated(PET_CHANNELS.quit, () => {
    app.quit();
  });
}

/** 启动时把磁盘上的设置同步进窗口（在任何窗口创建之前调用） */
export async function restoreSettings(deps: IpcDependencies): Promise<PetSettings> {
  const settings = await loadPetSettings();
  const state = applyPetSettings(settings);

  broadcast(PET_CHANNELS.settingsChanged, settings);
  broadcast(PET_CHANNELS.stateChanged, state);
  deps.onSettingsApplied(settings);

  return settings;
}

export { loadPetSettings };
