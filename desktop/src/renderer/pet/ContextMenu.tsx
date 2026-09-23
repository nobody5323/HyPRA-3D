import { useEffect } from "react";

export interface ContextMenuItem {
  key: string;
  label: string;
  onSelect(): void;
  /** 勾选态（穿透 / 置顶这类开关项） */
  checked?: boolean;
  disabled?: boolean;
  /** 在它之前插入分隔线 */
  separatorBefore?: boolean;
}

export interface ContextMenuProps {
  /** 相对窗口的坐标（CSS 像素） */
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose(): void;
}

/** 菜单尺寸估算：用于把菜单夹在窗口内（桌宠窗只有 380×480，很容易溢出） */
const MENU_WIDTH = 168;
const ITEM_HEIGHT = 26;
const MENU_PADDING = 8;

/**
 * 右键菜单。
 *
 * 用渲染层实现而不是 Electron 原生菜单：桌宠窗是透明无边框的，
 * 原生菜单在 Windows 上会带系统配色，与桌宠风格割裂；原生菜单留给托盘。
 */
export function ContextMenu({ x, y, items, onClose }: ContextMenuProps) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", closeOnEscape);

    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  const menuHeight = items.length * ITEM_HEIGHT + MENU_PADDING * 2;

  return (
    <>
      {/* 透明遮罩：点空白处关闭菜单 */}
      <div className="petMenuBackdrop" onPointerDown={onClose} role="presentation" />
      <div
        className="petMenu"
        role="menu"
        style={{
          left: Math.max(4, Math.min(x, window.innerWidth - MENU_WIDTH - 4)),
          top: Math.max(4, Math.min(y, window.innerHeight - menuHeight - 4)),
        }}
      >
        {items.map((item) => (
          <div key={item.key}>
            {item.separatorBefore ? <div className="petMenuSeparator" /> : null}
            <button
              type="button"
              role="menuitemcheckbox"
              aria-checked={item.checked}
              className="petMenuItem"
              disabled={item.disabled}
              onClick={() => {
                item.onSelect();
                onClose();
              }}
            >
              <span className="petMenuCheck">{item.checked ? "✓" : ""}</span>
              {item.label}
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
