import { BrowserWindow, screen } from "electron";
import path from "node:path";

import { DEFAULT_PET_SETTINGS, type PetDragPoint, type PetSettings, type PetStateListener, type PetWindowState } from "../shared/ipc";
import {
  applyDragDelta,
  clampBoundsToWorkArea,
  defaultPetBounds,
  resizeBoundsForScale,
  scalePetSize,
  PET_BASE_SIZE,
  type Rect,
} from "./petGeometry";
import { loadPetWindowPosition, savePetWindowPosition } from "./petPositionStore";
import { hardenWindowNavigation } from "./windowSecurity";

let petWindow: BrowserWindow | null = null;
let dragStart: { pointer: PetDragPoint; origin: { x: number; y: number } } | null = null;
let displayListenersRegistered = false;

/** 运行时设置（由 ipc 层从磁盘读出后 apply 进来） */
let settings: PetSettings = { ...DEFAULT_PET_SETTINGS };

/** 页面来源（静态资源服务或 Vite dev server），由主进程启动时注入 */
let pageOrigin = "";

/** 穿透模式下的临时解锁态（不持久化） */
let controlInteractive = false;

const stateListeners = new Set<PetStateListener>();

/** 注入页面来源。必须在显示窗口之前调用一次 */
export function configurePetWindow(options: { origin: string }): void {
  pageOrigin = options.origin.replace(/\/$/, "");
}

/** 应用设置（缩放 / 置顶 / 穿透），由 ipc 层在设置变化时调用 */
export function applyPetSettings(next: PetSettings): PetWindowState {
  const scaleChanged = next.scale !== settings.scale;

  settings = next;
  controlInteractive = false;

  if (scaleChanged) {
    applyScale();
  }

  applyWindowState();

  return emitStateChanged();
}

/** 当前设置快照 */
export function getPetSettings(): PetSettings {
  return { ...settings };
}

/**
 * 取当前可用的桌宠窗口。
 *
 * 所有窗口操作都先经过它：窗口可能尚未创建、已关闭或正在销毁，
 * 统一返回 undefined 能让每个操作都写成「取到就做、取不到就返回」，不必到处判空。
 */
function alivePetWindow(): BrowserWindow | undefined {
  return petWindow && !petWindow.isDestroyed() ? petWindow : undefined;
}

function windowRect(window: BrowserWindow): Rect {
  const bounds = window.getBounds();

  return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
}

function workAreaFor(rect: Rect): Rect {
  return screen.getDisplayMatching(rect).workArea;
}

/**
 * 取当前光标位置（**屏幕坐标**）。
 *
 * 主进程的光标位置是拖拽的真实依据：拖拽期间窗口跟着光标移动，
 * 渲染层上报的窗口内坐标会随之漂移，只有它不会。
 * 渲染层传来的值仅作为取不到光标时的兜底。
 */
function currentPointer(fallback: PetDragPoint): PetDragPoint {
  const point = screen.getCursorScreenPoint();

  return Number.isFinite(point.x) && Number.isFinite(point.y) ? point : fallback;
}

/**
 * 重新施加窗口属性。
 *
 * 必须重复调用：Windows 上窗口在 show / focus / blur / restore 之后
 * 会丢掉「置顶」与「鼠标穿透」状态，只在创建时设置一次是不够的。
 */
function applyWindowState(): void {
  const window = alivePetWindow();

  if (!window) {
    return;
  }

  window.setAlwaysOnTop(settings.alwaysOnTop, "screen-saver");
  window.setIgnoreMouseEvents(settings.clickThrough && !controlInteractive, { forward: true });
}

/** 按当前缩放重算窗口尺寸（底边中点锚定），并保存新位置 */
function applyScale(): void {
  const window = alivePetWindow();

  if (!window) {
    return;
  }

  const bounds = windowRect(window);
  const next = resizeBoundsForScale(bounds, workAreaFor(bounds), settings.scale);

  if (next.width !== bounds.width || next.height !== bounds.height) {
    window.setBounds(next, false);
    void savePetWindowPosition({ x: next.x, y: next.y }).catch(() => undefined);
  }
}

function snapshot(): PetWindowState {
  const window = alivePetWindow();

  return {
    visible: Boolean(window?.isVisible()),
    clickThrough: settings.clickThrough,
    controlInteractive,
    scale: settings.scale,
    alwaysOnTop: settings.alwaysOnTop,
  };
}

function emitStateChanged(): PetWindowState {
  const state = snapshot();

  for (const listener of stateListeners) {
    listener(state);
  }

  return state;
}

/** 订阅窗口状态变化（返回取消订阅函数） */
export function onPetWindowStateChanged(listener: PetStateListener): () => void {
  stateListeners.add(listener);

  return () => {
    stateListeners.delete(listener);
  };
}

/** 显示器变化（拔掉外接屏 / 改分辨率）后把窗口拉回可见范围 */
function recoverWindowIntoWorkArea(): void {
  const window = alivePetWindow();

  if (!window) {
    return;
  }

  const bounds = windowRect(window);
  const clamped = clampBoundsToWorkArea(bounds, workAreaFor(bounds));

  if (clamped.x !== bounds.x || clamped.y !== bounds.y) {
    window.setBounds(clamped, false);
    void savePetWindowPosition({ x: clamped.x, y: clamped.y }).catch(() => undefined);
  }
}

function registerDisplayRecoveryListeners(): void {
  if (displayListenersRegistered) {
    return;
  }

  displayListenersRegistered = true;
  screen.on("display-removed", recoverWindowIntoWorkArea);
  screen.on("display-metrics-changed", recoverWindowIntoWorkArea);
}

/**
 * 决定窗口的初始位置与尺寸。
 *
 * 有存档位置就沿用，但会按它所在的显示器夹一次（存档可能来自已拔掉的屏幕）；
 * 没有存档则停在主屏右下角。
 */
async function resolveInitialBounds(): Promise<Rect> {
  const primaryWorkArea = screen.getPrimaryDisplay().workArea;
  const saved = await loadPetWindowPosition();

  if (!saved) {
    return defaultPetBounds(primaryWorkArea, settings.scale);
  }

  const size = scalePetSize(PET_BASE_SIZE, settings.scale, primaryWorkArea);
  const bounds: Rect = { ...saved, ...size };

  return clampBoundsToWorkArea(bounds, workAreaFor(bounds));
}

/** 桌宠窗地址：开发期指向 Vite dev server，生产期指向本地静态资源服务 */
function resolvePetWindowUrl(): string {
  if (process.env.HYPRA_DEV_SERVER_URL) {
    return `${process.env.HYPRA_DEV_SERVER_URL.replace(/\/$/, "")}/pet.html`;
  }

  if (!pageOrigin) {
    // 走到这里说明 configurePetWindow 没被调用，属于接线错误，直接暴露出来
    throw new Error("桌宠页面来源未配置（应先调用 configurePetWindow）");
  }

  return `${pageOrigin}/pet.html`;
}

function createPetWindow(initialBounds: Rect): BrowserWindow {
  const window = new BrowserWindow({
    ...initialBounds,
    frame: false, // 无边框：桌面宠物不能有标题栏
    transparent: true, // 透明底：只有角色本身可见
    resizable: false,
    maximizable: false,
    minimizable: false,
    skipTaskbar: true, // 不进任务栏，入口在托盘
    hasShadow: false,
    backgroundColor: "#00000000",
    show: false, // 等首帧渲染完再显示，避免白屏闪一下
    title: "HyPRA 桌宠",
    webPreferences: {
      preload: path.join(__dirname, "../preload/pet.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      // 置顶窗失焦时仍要持续绘制（呼吸动画 / 待机动作都靠它）
      backgroundThrottling: false,
    },
  });

  hardenWindowNavigation(window);

  window.once("ready-to-show", () => window.show());
  window.on("show", applyWindowState);
  window.on("focus", applyWindowState);
  window.on("blur", applyWindowState);
  window.on("restore", applyWindowState);
  window.on("closed", () => {
    if (petWindow !== window) {
      return;
    }

    petWindow = null;
    controlInteractive = false;
    dragStart = null;
    emitStateChanged();
  });

  petWindow = window;
  applyWindowState();

  return window;
}

/** 显示桌宠窗（已存在则复用并聚焦） */
export async function showPetWindow(): Promise<PetWindowState> {
  registerDisplayRecoveryListeners();

  const existing = alivePetWindow();

  if (existing) {
    existing.show();
    existing.focus();
    applyWindowState();

    return emitStateChanged();
  }

  const window = createPetWindow(await resolveInitialBounds());
  await window.loadURL(resolvePetWindowUrl());

  return emitStateChanged();
}

/** 隐藏桌宠窗（托盘可再唤出；不销毁，对话状态得以保留） */
export function hidePetWindow(): PetWindowState {
  alivePetWindow()?.hide();

  return emitStateChanged();
}

/** 显示 / 隐藏切换（托盘与全局快捷键用） */
export async function togglePetWindow(): Promise<PetWindowState> {
  const window = alivePetWindow();

  if (window?.isVisible()) {
    return hidePetWindow();
  }

  return showPetWindow();
}

/** 关闭桌宠窗（退出流程用） */
export function destroyPetWindow(): void {
  alivePetWindow()?.destroy();
}

export function getPetWindowState(): PetWindowState {
  return snapshot();
}

/** 开关点击穿透 */
export function setPetWindowClickThrough(value: boolean): PetWindowState {
  settings = { ...settings, clickThrough: value };

  // 每次切换穿透都从「未进入角色区域」重新开始，避免解锁入口莫名常显
  controlInteractive = false;
  applyWindowState();

  return emitStateChanged();
}

/** 上报光标是否位于角色区域（穿透模式下临时恢复可点击，用于点击「解锁」） */
export function setPetWindowControlInteractive(value: boolean): PetWindowState {
  if (!alivePetWindow()) {
    return snapshot();
  }

  controlInteractive = settings.clickThrough && value;
  applyWindowState();

  return emitStateChanged();
}

/** 开始拖拽（point 为按下瞬间的屏幕坐标） */
export function startPetWindowDrag(point: PetDragPoint): void {
  const window = alivePetWindow();

  if (!window || settings.clickThrough) {
    return;
  }

  const bounds = windowRect(window);

  dragStart = {
    pointer: currentPointer(point),
    origin: { x: bounds.x, y: bounds.y },
  };
}

/** 拖拽中（point 为当前屏幕坐标） */
export function movePetWindowDrag(point: PetDragPoint): void {
  const window = alivePetWindow();

  if (!window || !dragStart || settings.clickThrough) {
    return;
  }

  const next = applyDragDelta(dragStart.origin, dragStart.pointer, currentPointer(point));
  const bounds = windowRect(window);

  window.setBounds({ ...next, width: bounds.width, height: bounds.height }, false);
}

/** 结束拖拽：夹进工作区并保存位置 */
export async function endPetWindowDrag(): Promise<void> {
  const window = alivePetWindow();

  if (!window || !dragStart) {
    dragStart = null;

    return;
  }

  dragStart = null;

  const bounds = windowRect(window);
  const clamped = clampBoundsToWorkArea(bounds, workAreaFor(bounds));

  if (clamped.x !== bounds.x || clamped.y !== bounds.y) {
    window.setBounds(clamped, false);
  }

  await savePetWindowPosition({ x: clamped.x, y: clamped.y }).catch((error: unknown) => {
    // 位置存不下来不影响使用，只是下次启动会回到默认位置
    console.warn("[pet] 保存窗口位置失败", error);
  });
}
