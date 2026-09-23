/**
 * 主进程与渲染进程共享的 IPC 契约。
 *
 * 本文件被三处引用，因此**不允许** import electron / react / node 内置模块：
 *   - src/main/**      （CommonJS，Node 环境）
 *   - src/preload/**   （CommonJS，sandbox preload）
 *   - src/renderer/**  （ESM，浏览器环境）
 * 这里只放类型与常量。
 */

/** 缩放范围（与设置面板的步进一致） */
export const MIN_PET_SCALE = 0.7;
export const MAX_PET_SCALE = 1.5;
export const PET_SCALE_STEP = 0.05;

/** 桌宠窗口与渲染共用的可持久化设置 */
export interface PetSettings {
  /** 桌宠缩放（0.7–1.5） */
  scale: number;
  /** 窗口是否置顶 */
  alwaysOnTop: boolean;
  /** 是否处于点击穿透模式 */
  clickThrough: boolean;
}

/** 桌宠窗口对外可见的状态快照 */
export interface PetWindowState {
  /** 窗口当前是否可见 */
  visible: boolean;
  /**
   * 是否处于「点击穿透」模式。
   *
   * 穿透时鼠标事件仍会转发给本窗口（Electron 的 `forward: true`），
   * 因此渲染层依然能知道光标位置，用来显示「解锁」入口。
   */
  clickThrough: boolean;
  /** 穿透模式下光标是否已进入角色区域（此时临时恢复可点击） */
  controlInteractive: boolean;
  /** 当前缩放 */
  scale: number;
  /** 当前是否置顶 */
  alwaysOnTop: boolean;
}

/** 运行环境信息（渲染层据此连后端、显示版本） */
export interface PetEnvironment {
  /** 后端 API 基址（FastAPI） */
  apiBase: string;
  /** 桌面端版本 */
  appVersion: string;
  /** 运行平台（win32 / darwin / linux） */
  platform: string;
  /** 是否为开发模式（加载 Vite dev server） */
  development: boolean;
}

/**
 * 拖拽坐标。
 *
 * 必须是**屏幕坐标**（渲染层的 `event.screenX/screenY`）：拖拽过程中窗口本身在移动，
 * 窗口内的相对坐标会跟着漂移，只有屏幕坐标在拖拽期间保持稳定。
 */
export interface PetDragPoint {
  x: number;
  y: number;
}

/** 桌宠窗状态变化事件（主进程 → 渲染层） */
export type PetStateListener = (state: PetWindowState) => void;

/** 设置变化事件（主进程 → 渲染层） */
export type PetSettingsListener = (settings: PetSettings) => void;

/**
 * preload 通过 contextBridge 暴露给渲染层的**全部**桌面能力（`window.hyprPet`）。
 *
 * 渲染层拿不到 ipcRenderer / require / fs —— 只能调用这里列出的方法。
 */
export interface PetBridge {
  /** 读取当前窗口状态（首帧同步用） */
  getState(): Promise<PetWindowState>;
  /** 开关点击穿透 */
  setClickThrough(value: boolean): Promise<PetWindowState>;
  /** 上报「光标是否在角色区域」，供穿透模式临时恢复可点击 */
  setControlInteractive(value: boolean): Promise<PetWindowState>;
  /** 开始拖拽（传入按下瞬间的屏幕坐标） */
  startDrag(point: PetDragPoint): void;
  /** 拖拽中（传入当前屏幕坐标） */
  moveDrag(point: PetDragPoint): void;
  /** 结束拖拽（主进程负责夹进工作区并落盘位置） */
  endDrag(): void;
  /** 隐藏桌宠窗（托盘里可再唤出） */
  hide(): void;
  /** 退出桌面端 */
  quit(): void;
  /** 订阅窗口状态变化，返回取消订阅函数 */
  onStateChanged(listener: PetStateListener): () => void;
  /** 读取设置 */
  getSettings(): Promise<PetSettings>;
  /** 局部更新设置（主进程负责校验与落盘） */
  updateSettings(patch: Partial<PetSettings>): Promise<PetSettings>;
  /** 订阅设置变化，返回取消订阅函数 */
  onSettingsChanged(listener: PetSettingsListener): () => void;
  /** 读取运行环境信息 */
  getEnvironment(): Promise<PetEnvironment>;
}

/** IPC 通道名（主进程与渲染层共用，避免两边手写字符串写歪） */
export const PET_CHANNELS = {
  getState: "pet:get-state",
  stateChanged: "pet:state-changed",
  setClickThrough: "pet:set-click-through",
  setControlInteractive: "pet:set-control-interactive",
  startDrag: "pet:start-drag",
  moveDrag: "pet:move-drag",
  endDrag: "pet:end-drag",
  hide: "pet:hide",
  quit: "pet:quit",
  getSettings: "pet:get-settings",
  updateSettings: "pet:update-settings",
  settingsChanged: "pet:settings-changed",
  getEnvironment: "pet:get-environment",
} as const;

/** 通道名的键（preload 因 sandbox 限制只能内联通道名，靠它做类型校验） */
export type PetChannelName = keyof typeof PET_CHANNELS;

/** 合法的通道名字面量（同上） */
export type PetChannel = (typeof PET_CHANNELS)[PetChannelName];

/** 设置默认值（主进程与渲染层共用同一份，避免两边默认值不一致） */
export const DEFAULT_PET_SETTINGS: PetSettings = {
  scale: 1,
  alwaysOnTop: true,
  clickThrough: false,
};

/**
 * 把任意输入归一化成合法设置。
 *
 * 设置来自磁盘上的 JSON（用户可手改）或 IPC 入参（不可信），
 * 因此每个字段都要校验，非法值回落默认值而不是让窗口处于坏状态。
 */
export function normalizePetSettings(value: unknown): PetSettings {
  const source = (value ?? {}) as Partial<Record<keyof PetSettings, unknown>>;
  const scale = Number(source.scale);

  return {
    scale: Number.isFinite(scale)
      ? Math.min(MAX_PET_SCALE, Math.max(MIN_PET_SCALE, roundScale(scale)))
      : DEFAULT_PET_SETTINGS.scale,
    alwaysOnTop:
      typeof source.alwaysOnTop === "boolean"
        ? source.alwaysOnTop
        : DEFAULT_PET_SETTINGS.alwaysOnTop,
    clickThrough:
      typeof source.clickThrough === "boolean"
        ? source.clickThrough
        : DEFAULT_PET_SETTINGS.clickThrough,
  };
}

/** 步进的倒数（0.05 → 20）：用整数运算对齐，避免 14 × 0.05 = 0.7000000000000001 */
const SCALE_DIVISOR = Math.round(1 / PET_SCALE_STEP);

/** 对齐到步进的整数倍（也不产生浮点尾巴） */
export function roundScale(scale: number): number {
  return Math.round(scale * SCALE_DIVISOR) / SCALE_DIVISOR;
}
