import { contextBridge, ipcRenderer } from "electron";
import type {
  DesktopContextSnapshot,
  PetBridge,
  PetChannel,
  PetChannelName,
  PetDragPoint,
  PetEnvironment,
  PetSettings,
  PetSettingsListener,
  PetStateListener,
  PetWindowState,
} from "../shared/ipc";

/**
 * 通道名在这里**内联**，不能 `import { PET_CHANNELS } from "../shared/ipc"`。
 *
 * 原因：preload 跑在 sandbox 里，`require` 只认 `electron` 与极少数内置模块，
 * 加载任何本地文件都会直接报错（表现为 `window.hyprPet` 根本没被注入）。
 *
 * 类型安全由 `Record<PetChannelName, PetChannel>` 兜底：shared 里增删通道、
 * 或这里把字符串写歪，tsc 都会直接报错。
 */
const CHANNELS: Record<PetChannelName, PetChannel> = {
  getState: "pet:get-state",
  stateChanged: "pet:state-changed",
  setClickThrough: "pet:set-click-through",
  setControlInteractive: "pet:set-control-interactive",
  setChatPanel: "pet:set-chat-panel",
  startDrag: "pet:start-drag",
  moveDrag: "pet:move-drag",
  endDrag: "pet:end-drag",
  hide: "pet:hide",
  quit: "pet:quit",
  getSettings: "pet:get-settings",
  updateSettings: "pet:update-settings",
  settingsChanged: "pet:settings-changed",
  getEnvironment: "pet:get-environment",
  getDesktopContext: "pet:get-desktop-context",
};

/**
 * preload：渲染层能看到的**全部**桌面能力。
 *
 * 渲染层拿不到 ipcRenderer / require / fs，只能调用下面这些方法，
 * 因此「桌面端比 Web 端多出来的能力」在这一个文件里就能读完。
 */
const bridge: PetBridge = {
  getState: () => ipcRenderer.invoke(CHANNELS.getState) as Promise<PetWindowState>,

  setClickThrough: (value: boolean) =>
    ipcRenderer.invoke(CHANNELS.setClickThrough, value) as Promise<PetWindowState>,

  setControlInteractive: (value: boolean) =>
    ipcRenderer.invoke(CHANNELS.setControlInteractive, value) as Promise<PetWindowState>,

  setChatPanel: (open: boolean) =>
    ipcRenderer.invoke(CHANNELS.setChatPanel, open) as Promise<PetWindowState>,

  startDrag: (point: PetDragPoint) => {
    ipcRenderer.send(CHANNELS.startDrag, point);
  },

  moveDrag: (point: PetDragPoint) => {
    ipcRenderer.send(CHANNELS.moveDrag, point);
  },

  endDrag: () => {
    ipcRenderer.send(CHANNELS.endDrag);
  },

  hide: () => {
    ipcRenderer.send(CHANNELS.hide);
  },

  quit: () => {
    ipcRenderer.send(CHANNELS.quit);
  },

  onStateChanged: (listener: PetStateListener) => {
    const handler = (_event: Electron.IpcRendererEvent, state: PetWindowState) => {
      listener(state);
    };

    ipcRenderer.on(CHANNELS.stateChanged, handler);

    return () => {
      ipcRenderer.removeListener(CHANNELS.stateChanged, handler);
    };
  },

  getSettings: () => ipcRenderer.invoke(CHANNELS.getSettings) as Promise<PetSettings>,

  updateSettings: (patch: Partial<PetSettings>) =>
    ipcRenderer.invoke(CHANNELS.updateSettings, patch) as Promise<PetSettings>,

  onSettingsChanged: (listener: PetSettingsListener) => {
    const handler = (_event: Electron.IpcRendererEvent, settings: PetSettings) => {
      listener(settings);
    };

    ipcRenderer.on(CHANNELS.settingsChanged, handler);

    return () => {
      ipcRenderer.removeListener(CHANNELS.settingsChanged, handler);
    };
  },

  getEnvironment: () => ipcRenderer.invoke(CHANNELS.getEnvironment) as Promise<PetEnvironment>,

  getDesktopContext: (force = false) =>
    ipcRenderer.invoke(
      CHANNELS.getDesktopContext,
      force,
    ) as Promise<DesktopContextSnapshot>,
};

contextBridge.exposeInMainWorld("hyprPet", bridge);
