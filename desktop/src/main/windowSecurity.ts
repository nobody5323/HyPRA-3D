import { shell } from "electron";
import type { BrowserWindow } from "electron";

/**
 * 收紧窗口导航：桌宠窗只应加载自己的页面。
 *
 * 三件事：
 *   1. 新窗口请求一律拒绝，外链交给系统浏览器；
 *   2. 页面内跳转到别的地址一律拦截（页面只应停在 pet.html）；
 *   3. 禁止嵌入 `<webview>`。
 *
 * 目的是让「渲染层被注入任意内容」这件事无法把桌宠窗变成任意页面容器。
 */
export function hardenWindowNavigation(window: BrowserWindow): void {
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) {
      void shell.openExternal(url);
    }

    return { action: "deny" };
  });

  window.webContents.on("will-navigate", (event, url) => {
    // 允许同地址刷新（Vite HMR 整页重载会走到这里）
    if (url !== window.webContents.getURL()) {
      event.preventDefault();
    }
  });

  window.webContents.on("will-attach-webview", (event) => {
    event.preventDefault();
  });
}
