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
/**
 * 对话面板展开时，窗口额外增加的宽度（**缩放前的基准值**）。
 *
 * ⚠️ 必须与 `renderer/pet/pet.css` 的 `--pet-chat-panel-width` 一致：
 * 主进程负责把窗口加宽这么多，CSS 负责把模型区锁回左侧原宽度，
 * 两边说的必须是同一个数。
 *
 * 190px 的来历：它是**面板自己需要的宽度**（消息气泡 + 输入框 + 发送键），
 * 与窗口本体多宽无关。窗口本体已从 380px 收到 280px（见 `petGeometry.ts`），
 * 但面板收窄只会让消息挤成一条，所以这个数不跟着动。
 */
export const PET_CHAT_PANEL_BASE_WIDTH = 190;

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
  /**
   * 对话面板是否展开。
   *
   * 展开时窗口会**向右加宽** `PET_CHAT_PANEL_BASE_WIDTH`，面板占新多出来的那一栏，
   * 模型区则由 CSS 锁在左侧原宽度——所以模型不移动、也不缩放。
   */
  chatPanelOpen: boolean;
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

// =============================================================
// 桌面情景（感知层的数据来源之一）
// =============================================================

/**
 * 一次桌面情景采集的结果（主进程采集 → 渲染层上报后端）。
 *
 * 设计见 `docs/proactive-multimodal.md` §4.2。字段刻意都是**原始值**，
 * 主进程不做任何判断（「算不算忙碌」「该不该关心」全在后端）——
 * 主进程只负责「把机器上看得见的东西读出来」。
 *
 * **不含任何密钥、不含屏幕内容、不含摄像头**：这几项要么在渲染层（凭证），
 * 要么已被明确否决（连续屏幕 / 摄像头，见 §4.4 决策）。
 */
export interface DesktopContextSnapshot {
  /** 系统媒体（Windows SMTC）：正在播放的曲名 */
  now_playing_title: string;
  /** 正在播放的艺术家 */
  now_playing_artist: string;
  /** 前台窗口标题（**默认不注入提示词**，见 §4.6） */
  foreground_title: string;
  /** 前台窗口的进程名（如 `Code.exe`；忙碌判定用，不进提示词） */
  foreground_process: string;
  /** 用户空闲秒数（无键鼠输入） */
  idle_seconds: number;
  /** 客户端本地时间 HH:MM（由客户端给，避免主机与用户时区不一致） */
  local_time: string;
  /** 客户端本地日期 YYYY-MM-DD */
  local_date: string;
}

/** 桌面情景采集失败时的空值（字段齐全，便于调用方直接展开） */
export const EMPTY_DESKTOP_CONTEXT: DesktopContextSnapshot = {
  now_playing_title: "",
  now_playing_artist: "",
  foreground_title: "",
  foreground_process: "",
  idle_seconds: 0,
  local_time: "",
  local_date: "",
};

/**
 * 把任意输入归一化成合法的桌面情景快照。
 *
 * 采集结果来自 PowerShell 的 JSON（外部输出，不可信），且要跨 IPC 传进渲染层——
 * 因此每个字段都校验，非法值回落空值而不是让下游拿到 `undefined`。
 * 这一点在「字段缺失 → 后端 pydantic 报 422 → 上报静默失败」这条链路上尤其重要。
 */
export function normalizeDesktopContext(value: unknown): DesktopContextSnapshot {
  const source = (value ?? {}) as Record<string, unknown>;
  const text = (key: string): string =>
    typeof source[key] === "string" ? (source[key] as string).trim() : "";
  const idle = Number(source.idle_seconds);

  return {
    now_playing_title: text("now_playing_title"),
    now_playing_artist: text("now_playing_artist"),
    foreground_title: text("foreground_title"),
    foreground_process: text("foreground_process"),
    idle_seconds: Number.isFinite(idle) && idle >= 0 ? idle : 0,
    local_time: text("local_time"),
    local_date: text("local_date"),
  };
}

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
  /**
   * 展开 / 收起对话面板。
   *
   * 主进程据此**加宽或收回窗口**（向右扩展，左边缘不动），
   * 面板本身由渲染层绘制——桌面端不重写聊天逻辑，只是给它腾一块地方。
   */
  setChatPanel(open: boolean): Promise<PetWindowState>;
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
  /**
   * 采集一次桌面情景（正在听的歌 / 前台窗口 / 空闲秒数 / 本地时间）。
   *
   * **只采集，不上报**：渲染层拿到后自己 POST 给后端
   * （主进程不直连后端，见 `docs/desktop-pet.md` §4）。
   *
   * 非 Windows 平台只回「时间 + 空闲」——深夜关怀只靠这两项就能工作，
   * 其余字段为空表示「拿不到」，而不是出错。
   */
  getDesktopContext(force?: boolean): Promise<DesktopContextSnapshot>;
}

/** IPC 通道名（主进程与渲染层共用，避免两边手写字符串写歪） */
export const PET_CHANNELS = {
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

// =============================================================
// 受管服务（桌面端代管的本机进程）
// =============================================================

/**
 * 受管服务标识。
 *
 * 三个：向量库、后端、Web 前端。
 *
 * Web 前端（Next）是受管服务，但**不在「随程序自动启动」之列**
 * （见 `serviceManager.ts` 的 `startAll`）：它属于另一种部署形态，只有用户
 * 明确点了「本机服务 → 启动」或「以 Web 模式启动」才会被拉起来。
 * 若跟着自动启动走，每次开桌宠都会白白多出一个 Node 服务——而桌宠根本不用它。
 */
export type ServiceId = "qdrant" | "backend" | "frontend";

/**
 * 服务状态。
 *
 * - `stopped`  未运行
 * - `starting` 已拉起，正在等健康检查通过
 * - `ready`    健康检查通过（进程由本程序启动，也由本程序停止）
 * - `adopted`  端口上已有同类服务在跑（**不是本程序启动的**）——不接管、不停止
 * - `failed`   启动失败或就绪超时
 */
export type ServicePhase = "stopped" | "starting" | "ready" | "adopted" | "failed";

/** 一个受管服务的状态快照（渲染层直接展示，不需要再解释） */
export interface ServiceState {
  id: ServiceId;
  phase: ServicePhase;
  /** 进程 PID；`adopted` / 未运行 / 启动失败时为 null（那些进程不属于本程序） */
  pid: number | null;
  /** 状态说明或失败原因（界面直接显示这句话） */
  detail: string;
  /** 健康检查地址（排障时能看到在探哪里） */
  healthUrl: string;
}

/**
 * 服务相关设置。
 *
 * 与 `ConsoleSettings` 分开存：那边是界面偏好（Web 端地址），这边是**进程怎么启动**
 * （自动启动、路径覆盖）。分开的另一个好处是：用户手改坏一个路径，
 * 不会连带把控制台的其它偏好一起顶掉。
 */
export interface ServiceSettings {
  /** 启动桌面端时自动拉起服务（默认开——这正是「一键启动」的含义） */
  autoStart: boolean;
  /** 后端是否带 `--reload`（会产生 reloader + worker 两级子进程，收尾更难，默认关） */
  backendReload: boolean;
  /** 项目根覆盖（留空 = 从若干候选目录自动探测） */
  projectRoot: string;
  /** Python 解释器覆盖（留空 = 项目根下的 .venv） */
  pythonPath: string;
  /** Qdrant 可执行文件覆盖（留空 = 项目根下的 _local/qdrant） */
  qdrantPath: string;
}

export const DEFAULT_SERVICE_SETTINGS: ServiceSettings = {
  autoStart: true,
  backendReload: false,
  projectRoot: "",
  pythonPath: "",
  qdrantPath: "",
};

/** 路径类字段名（校验与归一化共用一份，避免两处各写一遍字符串） */
export const SERVICE_PATH_FIELDS = ["projectRoot", "pythonPath", "qdrantPath"] as const;

/** 归一化单条路径覆盖：去首尾空白；非字符串一律当「没填」 */
function normalizePathOverride(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * 把任意输入归一化成合法服务设置。
 *
 * 布尔字段只在**确实是布尔值**时才采纳：设置文件是用户可手改的，
 * 写了个 `"yes"` 就悄悄把自动启动关掉，比明确的报错难查得多。
 */
export function normalizeServiceSettings(value: unknown): ServiceSettings {
  const source = (value ?? {}) as Record<string, unknown>;

  return {
    autoStart:
      typeof source.autoStart === "boolean"
        ? source.autoStart
        : DEFAULT_SERVICE_SETTINGS.autoStart,
    backendReload:
      typeof source.backendReload === "boolean"
        ? source.backendReload
        : DEFAULT_SERVICE_SETTINGS.backendReload,
    projectRoot: normalizePathOverride(source.projectRoot),
    pythonPath: normalizePathOverride(source.pythonPath),
    qdrantPath: normalizePathOverride(source.qdrantPath),
  };
}

/** 服务状态变化事件（主进程 → 渲染层） */
export type ServiceStateListener = (states: ServiceState[]) => void;

// =============================================================
// 程序控制台窗（console.html）
// =============================================================

/**
 * 控制台窗的可持久化设置。
 *
 * 与 `PetSettings` 分开存：桌宠设置管的是「窗口长什么样」，
 * 控制台设置管的是「程序怎么被启动」，两者语义与生命周期都不同。
 */
export interface ConsoleSettings {
  /** Web 端地址：「以 Web 模式启动」时用系统浏览器打开它 */
  webUrl: string;
}

export const DEFAULT_CONSOLE_SETTINGS: ConsoleSettings = {
  webUrl: "http://localhost:3000",
};

/**
 * 归一化 Web 端地址。
 *
 * **只允许 http/https**：这个值最终会交给 `shell.openExternal`，
 * 而 `file://`、`javascript:`、自定义协议都能借它拉起本机任意程序——
 * 设置文件是用户可手改的，不能当可信输入。
 */
export function normalizeWebUrl(value: unknown): string {
  const raw = String(value ?? "").trim();
  if (!raw) {
    return "";
  }

  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return "";
    }

    // 去掉末尾斜杠：拼子路径时不会出现 `//`
    const path = url.pathname.replace(/\/+$/, "");
    return `${url.origin}${path}`;
  } catch {
    return "";
  }
}

/**
 * 判断 Web 端地址是否指向**本机的前端默认端口**。
 *
 * 只有这种地址才由桌面端负责把本地前端拉起来。换成远程地址（前端部署在别处）
 * 或别的本地端口（用户自己起的前端）时，本地这个 Next 服务与它无关，
 * 起起来只会白占一个端口和一份内存。
 */
export function isLocalFrontendUrl(value: string, port: number): boolean {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();

    if (host !== "localhost" && host !== "127.0.0.1" && host !== "::1" && host !== "[::1]") {
      return false;
    }

    // 端口省略时取协议默认值：`https://localhost` 指的是 443，不是 3000
    const actual = url.port ? Number(url.port) : url.protocol === "https:" ? 443 : 80;

    return actual === port;
  } catch {
    return false;
  }
}

/** 把任意输入归一化成合法控制台设置（非法值回落默认，而不是存进去让功能永远不可用） */
export function normalizeConsoleSettings(value: unknown): ConsoleSettings {
  const source = (value ?? {}) as { webUrl?: unknown };
  const normalized = normalizeWebUrl(source.webUrl);

  return { webUrl: normalized || DEFAULT_CONSOLE_SETTINGS.webUrl };
}

/** 控制台窗的状态快照 */
export interface ConsoleState {
  /** 桌宠窗当前是否可见（控制台里「桌面模式」的运行状态按它显示） */
  petVisible: boolean;
  /** 桌宠窗的完整状态（缩放 / 穿透 / 置顶），控制台的「桌宠设置」直接读它 */
  petState: PetWindowState;
  /** 桌宠窗设置（与桌宠窗内的设置面板共用同一份，改完两边一致） */
  petSettings: PetSettings;
  settings: ConsoleSettings;
  environment: PetEnvironment;
  /** 受管服务（Qdrant / 后端 / Web 前端）的当前状态 */
  services: ServiceState[];
  /** 服务设置（自动启动 / reload / 路径覆盖） */
  serviceSettings: ServiceSettings;
}

/** 「以 Web 模式启动」的结果（失败时给出可读原因，界面直接展示） */
export interface OpenWebResult {
  ok: boolean;
  reason?: string;
}

/** 控制台窗的 IPC 通道名 */
export const CONSOLE_CHANNELS = {
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
} as const;

export type ConsoleChannelName = keyof typeof CONSOLE_CHANNELS;
export type ConsoleChannel = (typeof CONSOLE_CHANNELS)[ConsoleChannelName];

export type ConsoleStateListener = (state: ConsoleState) => void;
export type ConsoleSettingsListener = (settings: ConsoleSettings) => void;

/**
 * 控制台窗能看到的桌面能力（`window.hyprConsole`）。
 *
 * 刻意**不**把 `window.hyprPet` 合并进来：控制台是常规窗口，不需要拖拽、穿透、
 * 缩放这些桌宠专属能力，给它多佘入口只会扩大可被滥用的面。
 */
export interface ConsoleBridge {
  getState(): Promise<ConsoleState>;
  getSettings(): Promise<ConsoleSettings>;
  updateSettings(patch: Partial<ConsoleSettings>): Promise<ConsoleSettings>;
  /** 以桌面模式启动：显示桌宠窗 */
  showPet(): Promise<ConsoleState>;
  /** 收起桌宠窗（回到托盘） */
  hidePet(): Promise<ConsoleState>;
  /** 改桌宠设置（缩放 / 穿透 / 置顶）：与桌宠窗内的设置面板共用同一份设置 */
  updatePetSettings(patch: Partial<PetSettings>): Promise<ConsoleState>;
  /** 以 Web 模式启动：确保本机前端就绪（地址指向本机默认端口时），再用系统浏览器打开 Web 端地址 */
  openWeb(): Promise<OpenWebResult>;
  /** 启动一个受管服务（已就绪或已被外部接管时为无操作） */
  startService(id: ServiceId): Promise<ConsoleState>;
  /** 停止一个受管服务（被外部接管的不在此列：不会去停别人的进程） */
  stopService(id: ServiceId): Promise<ConsoleState>;
  /** 改服务设置（自动启动 / reload / 路径覆盖） */
  updateServiceSettings(patch: Partial<ServiceSettings>): Promise<ServiceSettings>;
  quit(): void;
  onStateChanged(listener: ConsoleStateListener): () => void;
  onSettingsChanged(listener: ConsoleSettingsListener): () => void;
  onServicesChanged(listener: ServiceStateListener): () => void;
}
