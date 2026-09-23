import { BrowserWindow } from "electron";
import path from "node:path";

import { hardenWindowNavigation } from "./windowSecurity";

/**
 * 程序控制台窗。
 *
 * 它与桌宠窗的定位完全不同：桌宠窗是常驻桌面的一只形象（无边框、透明、不进任务栏），
 * 控制台是**用户主动打开**的配置与调试台（有边框、可缩放、进任务栏）。
 *
 * 关闭行为是**隐藏**而不是销毁：调试模型时会在控制台与桌宠之间来回切，
 * 每次重建都要重新拉一遍模型清单与插件状态，没必要。真正退出走托盘或控制台里的「退出」。
 */

let consoleWindow: BrowserWindow | null = null;

/** 页面来源（静态资源服务或 Vite dev server），由主进程启动时注入 */
let pageOrigin = "";
let devServerUrl = "";

/** 初始尺寸：够放下「左侧分组导航 + 右侧内容」两栏，且不超过常见笔记本屏高 */
const WINDOW_DEFAULTS = {
  width: 1120,
  height: 760,
  minWidth: 900,
  minHeight: 560,
};

/** 注入页面来源。必须在显示窗口之前调用一次 */
export function configureConsoleWindow(options: { origin: string; devServerUrl?: string }): void {
  pageOrigin = options.origin.replace(/\/$/, "");
  devServerUrl = (options.devServerUrl ?? "").replace(/\/$/, "");
}

function aliveConsoleWindow(): BrowserWindow | undefined {
  return consoleWindow && !consoleWindow.isDestroyed() ? consoleWindow : undefined;
}

/** 控制台地址：开发期指向 Vite dev server，生产期指向本地静态资源服务 */
function resolveConsoleUrl(): string {
  if (devServerUrl) {
    return `${devServerUrl}/console.html`;
  }

  if (!pageOrigin) {
    // 走到这里说明 configureConsoleWindow 没被调用，属于接线错误，直接暴露出来
    throw new Error("控制台页面来源未配置（应先调用 configureConsoleWindow）");
  }

  return `${pageOrigin}/console.html`;
}

function createConsoleWindow(): BrowserWindow {
  const window = new BrowserWindow({
    ...WINDOW_DEFAULTS,
    show: false, // 等首帧渲染完再显示，避免白屏闪一下
    title: "HyPRA 程序控制台",
    // 与 Web 端底色一致：加载页面时不闪白
    backgroundColor: "#FAF7F2",
    // 这是配置台，不需要 File/Edit/View 菜单（Windows 上的菜单条只会挤占高度）
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "../preload/console.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  hardenWindowNavigation(window);

  window.once("ready-to-show", () => window.show());

  // 点关闭按钮 = 收起来（下次秒开）。退出流程走 destroyConsoleWindow()
  window.on("close", (event) => {
    event.preventDefault();
    window.hide();
  });

  window.on("closed", () => {
    if (consoleWindow !== window) {
      return;
    }

    consoleWindow = null;
  });

  consoleWindow = window;

  return window;
}

/** 显示控制台（已存在则复用、还原并聚焦） */
export async function showConsoleWindow(): Promise<void> {
  const existing = aliveConsoleWindow();

  if (existing) {
    if (existing.isMinimized()) {
      existing.restore();
    }

    existing.show();
    existing.focus();

    return;
  }

  const window = createConsoleWindow();

  await window.loadURL(resolveConsoleUrl());
}

/** 隐藏控制台（托盘可再唤出） */
export function hideConsoleWindow(): void {
  aliveConsoleWindow()?.hide();
}

/** 显示 / 隐藏切换（托盘用） */
export async function toggleConsoleWindow(): Promise<void> {
  const window = aliveConsoleWindow();

  if (window?.isVisible()) {
    window.hide();

    return;
  }

  await showConsoleWindow();
}

/**
 * 关闭控制台窗（退出流程用）。
 *
 * 用 `destroy()` 而不是 `close()`：前者不触发上面那个「关起来」的拦截。
 */
export function destroyConsoleWindow(): void {
  aliveConsoleWindow()?.destroy();
}

/** 控制台窗当前是否可见（托盘菜单文案用） */
export function isConsoleWindowVisible(): boolean {
  return Boolean(aliveConsoleWindow()?.isVisible());
}
