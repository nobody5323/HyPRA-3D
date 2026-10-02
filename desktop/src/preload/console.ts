import { contextBridge, ipcRenderer } from "electron";
import type {
  ConsoleBridge,
  ConsoleChannel,
  ConsoleChannelName,
  ConsoleSettings,
  ConsoleSettingsListener,
  ConsoleState,
  ConsoleStateListener,
  OpenWebResult,
  PetSettings,
  ServiceId,
  ServiceSettings,
  ServiceState,
  ServiceStateListener,
} from "../shared/ipc";

/**
 * 通道名在这里**内联**，不能 `import { CONSOLE_CHANNELS } from "../shared/ipc"`。
 *
 * 原因：preload 跑在 sandbox 里，`require` 只认 `electron` 与极少数内置模块，
 * 加载任何本地文件都会直接报错（表现为 `window.hyprConsole` 根本没被注入）。
 * 类型安全由 `Record<ConsoleChannelName, ConsoleChannel>` 兜底：shared 里增删通道、
 * 或这里把字符串写歪，tsc 都会直接报错。
 */
const CHANNELS: Record<ConsoleChannelName, ConsoleChannel> = {
  getState: "console:get-state",
  stateChanged: "console:state-changed",
  getSettings: "console:get-settings",
  settingsChanged: "console:settings-changed",
  updateSettings: "console:update-settings",
  showPet: "console:show-pet",
  hidePet: "console:hide-pet",
  updatePetSettings: "console:update-pet-settings",
  openWeb: "console:open-web",
  startService: "console:start-service",
  stopService: "console:stop-service",
  updateServiceSettings: "console:update-service-settings",
  servicesChanged: "console:services-changed",
  quit: "console:quit",
};

/**
 * preload：控制台窗能看到的**全部**桌面能力。
 *
 * 渲染层拿不到 ipcRenderer / require / fs，只能调用下面这些方法；
 * 因此「控制台比 Web 页面多出来的能力」在这一个文件里就能读完。
 */
const bridge: ConsoleBridge = {
  getState: () => ipcRenderer.invoke(CHANNELS.getState) as Promise<ConsoleState>,

  getSettings: () => ipcRenderer.invoke(CHANNELS.getSettings) as Promise<ConsoleSettings>,

  updateSettings: (patch: Partial<ConsoleSettings>) =>
    ipcRenderer.invoke(CHANNELS.updateSettings, patch) as Promise<ConsoleSettings>,

  showPet: () => ipcRenderer.invoke(CHANNELS.showPet) as Promise<ConsoleState>,

  hidePet: () => ipcRenderer.invoke(CHANNELS.hidePet) as Promise<ConsoleState>,

  updatePetSettings: (patch: Partial<PetSettings>) =>
    ipcRenderer.invoke(CHANNELS.updatePetSettings, patch) as Promise<ConsoleState>,

  openWeb: () => ipcRenderer.invoke(CHANNELS.openWeb) as Promise<OpenWebResult>,

  startService: (id: ServiceId) =>
    ipcRenderer.invoke(CHANNELS.startService, id) as Promise<ConsoleState>,

  stopService: (id: ServiceId) =>
    ipcRenderer.invoke(CHANNELS.stopService, id) as Promise<ConsoleState>,

  updateServiceSettings: (patch: Partial<ServiceSettings>) =>
    ipcRenderer.invoke(CHANNELS.updateServiceSettings, patch) as Promise<ServiceSettings>,

  quit: () => ipcRenderer.send(CHANNELS.quit),

  onStateChanged: (listener: ConsoleStateListener) => {
    const handler = (_event: Electron.IpcRendererEvent, state: ConsoleState) => {
      listener(state);
    };

    ipcRenderer.on(CHANNELS.stateChanged, handler);

    return () => {
      ipcRenderer.removeListener(CHANNELS.stateChanged, handler);
    };
  },

  onSettingsChanged: (listener: ConsoleSettingsListener) => {
    const handler = (_event: Electron.IpcRendererEvent, settings: ConsoleSettings) => {
      listener(settings);
    };

    ipcRenderer.on(CHANNELS.settingsChanged, handler);

    return () => {
      ipcRenderer.removeListener(CHANNELS.settingsChanged, handler);
    };
  },

  onServicesChanged: (listener: ServiceStateListener) => {
    const handler = (_event: Electron.IpcRendererEvent, states: ServiceState[]) => {
      listener(states);
    };

    ipcRenderer.on(CHANNELS.servicesChanged, handler);

    return () => {
      ipcRenderer.removeListener(CHANNELS.servicesChanged, handler);
    };
  },
};

contextBridge.exposeInMainWorld("hyprConsole", bridge);
