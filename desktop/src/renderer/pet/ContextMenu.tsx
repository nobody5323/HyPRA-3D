import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { MENU_MARGIN, placeContextMenu, type MenuPlacement } from "./pet-menu";

export interface ContextMenuItem {
  key: string;
  label: string;
  onSelect(): void;
  /** 勾选态（穿透 / 置顶这类开关项） */
  checked?: boolean;
  disabled?: boolean;
  /** 在它之前插入分隔线 */
  separatorBefore?: boolean;
  /**
   * 无障碍角色。
   *
   * 默认 `menuitemcheckbox`（各自独立的开关）；**互斥的一组**（如交互模式的两项）
   * 必须传 `menuitemradio`，否则读屏会把「两个都勾上」这种不可能的状态念出来。
   */
  role?: "menuitemcheckbox" | "menuitemradio";
  /** 该项的补充说明（如模式各会召回什么），显示在标签下方 */
  hint?: string;
}

export interface ContextMenuProps {
  /** 相对窗口的坐标（CSS 像素） */
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose(): void;
}

function readViewport(): { width: number; height: number } {
  return { width: window.innerWidth, height: window.innerHeight };
}

/**
 * 右键菜单。
 *
 * 用渲染层实现而不是 Electron 原生菜单：桌宠窗是透明无边框的，
 * 原生菜单在 Windows 上会带系统配色，与桌宠风格割裂；原生菜单留给托盘。
 *
 * 落位**按实测尺寸**算（规则与理由见 `pet-menu.ts`）。窗口只有 280×480，
 * 菜单贴边右键时很容易顶出去被 `overflow: hidden` 裁掉；旧实现用写死的
 * `188×26` 估算位置，既没乘 `--pet-scale` 也没算分隔线，用户当前 105% 的缩放下
 * 就已经会把最后一项「退出 HyPRA」裁掉。
 */
export function ContextMenu({ x, y, items, onClose }: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  /**
   * 窗口可视区尺寸。
   *
   * 渲染期就要它：得先给菜单套上 `max-width`（见下面的 style），
   * 否则菜单比窗口还宽时，量出来的是「撑破窗口」的那个宽度，据此算的落点会跑到窗口外。
   * 桌宠窗 `resizable: false`，但**对话面板展开会加宽窗口**，所以尺寸变化要跟着重算。
   */
  const [viewport, setViewport] = useState(readViewport);
  /** 落点；`null` = 还没量出来（首帧先藏住，量完再显示，不会闪一下） */
  const [placement, setPlacement] = useState<MenuPlacement | null>(null);

  const measure = useCallback(() => {
    const element = menuRef.current;

    if (!element) {
      return;
    }

    const rect = element.getBoundingClientRect();

    setPlacement(
      placeContextMenu({
        cursor: { x, y },
        // 实测尺寸：缩放、字号、hint 行数、分隔线全都已经体现在里面
        size: { width: rect.width, height: rect.height },
        viewport: readViewport(),
      }),
    );
  }, [x, y]);

  // 量尺寸必须在**绘制前**完成：否则菜单会先在 (0,0) 闪一下再跳到光标处
  useLayoutEffect(() => {
    measure();
  }, [measure]);

  useEffect(() => {
    const onResize = (): void => {
      setViewport(readViewport());
      measure();
    };

    window.addEventListener("resize", onResize);

    return () => window.removeEventListener("resize", onResize);
  }, [measure]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", closeOnEscape);

    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return (
    <>
      {/* 透明遮罩：点空白处关闭菜单 */}
      <div className="petMenuBackdrop" onPointerDown={onClose} role="presentation" />
      <div
        ref={menuRef}
        className="petMenu"
        role="menu"
        style={{
          left: placement?.left ?? 0,
          top: placement?.top ?? 0,
          // 宽度上限与落位用的是同一个间隙常量，两边不会漂移
          maxWidth: Math.max(0, viewport.width - MENU_MARGIN * 2),
          // 装不下才限高并滚动；装得下时不设，避免平白多出一条滚动条
          maxHeight: placement?.maxHeight ?? undefined,
          overflowY: placement?.maxHeight ? "auto" : undefined,
          visibility: placement ? "visible" : "hidden",
        }}
      >
        {items.map((item) => (
          <div key={item.key}>
            {item.separatorBefore ? <div className="petMenuSeparator" /> : null}
            <button
              type="button"
              role={item.role ?? "menuitemcheckbox"}
              aria-checked={item.checked}
              className="petMenuItem"
              disabled={item.disabled}
              onClick={() => {
                item.onSelect();
                onClose();
              }}
            >
              <span className="petMenuCheck">{item.checked ? "✓" : ""}</span>
              <span className="petMenuLabel">
                {item.label}
                {item.hint ? <span className="petMenuHint">{item.hint}</span> : null}
              </span>
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
