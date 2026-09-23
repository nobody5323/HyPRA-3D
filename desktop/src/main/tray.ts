import { Menu, Tray, app, nativeImage } from "electron";

import { MAX_PET_SCALE, MIN_PET_SCALE, PET_SCALE_STEP } from "../shared/ipc";
import { createTrayIconPng } from "./trayIcon";

/**
 * 系统托盘。
 *
 * 桌宠窗没有任务栏按钮（`skipTaskbar`），托盘就是它唯一的「常驻入口」：
 * 隐藏后从托盘唤出、退出也只能从这里走，因此菜单必须始终反映当前状态。
 */

export interface TrayHandlers {
  /** 显示 / 隐藏桌宠窗 */
  toggleVisibility(): void;
  /** 开关点击穿透 */
  toggleClickThrough(): void;
  /** 开关置顶 */
  toggleAlwaysOnTop(): void;
  /** 调整缩放（direction: 1 放大 / -1 缩小） */
  changeScale(direction: 1 | -1): void;
}

export interface TrayState {
  visible: boolean;
  clickThrough: boolean;
  alwaysOnTop: boolean;
  scale: number;
}

export interface AppTray {
  /** 状态变化后刷新菜单（勾选项要跟着变） */
  refresh(state: TrayState): void;
  destroy(): void;
}

export function createAppTray(handlers: TrayHandlers, state: TrayState): AppTray {
  const tray = new Tray(nativeImage.createFromBuffer(createTrayIconPng()));
  let current = state;

  const buildMenu = (): Menu =>
    Menu.buildFromTemplate([
      {
        label: current.visible ? "隐藏桌宠" : "显示桌宠",
        click: () => handlers.toggleVisibility(),
      },
      { type: "separator" },
      {
        label: "点击穿透",
        type: "checkbox",
        checked: current.clickThrough,
        click: () => handlers.toggleClickThrough(),
      },
      {
        label: "窗口置顶",
        type: "checkbox",
        checked: current.alwaysOnTop,
        click: () => handlers.toggleAlwaysOnTop(),
      },
      { type: "separator" },
      {
        label: `放大（当前 ${Math.round(current.scale * 100)}%）`,
        enabled: current.scale < MAX_PET_SCALE,
        click: () => handlers.changeScale(1),
      },
      {
        label: "缩小",
        enabled: current.scale > MIN_PET_SCALE,
        click: () => handlers.changeScale(-1),
      },
      { type: "separator" },
      {
        label: "退出 HyPRA",
        click: () => {
          // app.quit 会走 before-quit 的清理流程（销毁窗口、关闭静态资源服务）
          app.quit();
        },
      },
    ]);

  tray.setToolTip("HyPRA 桌宠");
  tray.setContextMenu(buildMenu());
  // 左键单击直接显示/隐藏，这是桌宠最常用的操作
  tray.on("click", () => handlers.toggleVisibility());

  current = state;

  return {
    refresh(next: TrayState) {
      current = next;
      tray.setContextMenu(buildMenu());
      tray.setToolTip(
        `HyPRA 桌宠 · ${next.visible ? "显示中" : "已隐藏"} · ${Math.round(next.scale * 100)}%`,
      );
    },
    destroy() {
      tray.destroy();
    },
  };
}

/** 缩放步进（托盘与设置面板共用同一份规则） */
export function nextScale(current: number, direction: 1 | -1): number {
  const stepped = Math.round((current + direction * PET_SCALE_STEP) / PET_SCALE_STEP) * PET_SCALE_STEP;

  return Math.min(MAX_PET_SCALE, Math.max(MIN_PET_SCALE, Number(stepped.toFixed(2))));
}
