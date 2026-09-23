"use client";

/**
 * 文风切换器（A/B 对比演示用）。
 *
 * 清单来自后端 `GET /chat/styles`（对应 app/prompts/style/presets/*.yaml）——
 * 预设内容与代码分离，新增或调整 YAML 不需要改前端代码。
 * 切换后下一轮对话即生效（前端仅传 style_id）。
 *
 * 无障碍要点（审计 A2 / A10）：
 * - 触发器暴露 `aria-expanded` / `aria-haspopup="listbox"` / `aria-controls`；
 * - 弹出层是 `role="listbox"`，每项是 `role="option"` + `aria-selected`；
 * - 键盘完整可用：Enter/Space 打开、↑↓ 移动、Home/End 首尾、Esc 关闭并归还焦点、点击外部关闭。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import type { StyleCatalog, StyleOption } from "@/lib/api/types";

const TRIGGER_ID = "style-trigger";
const LIST_ID = "style-listbox";

/**
 * 关闭文风层的特殊值（后端约定，见 docs/st-preset-compat.md §10）。
 *
 * 为什么需要它：文风预设的 sampling 优先级**最高**，会盖过酒馆预设作者自己的
 * 采样意图；导入酒馆预设的用户要“纯预设体验”时必须能关掉这一层。
 */
const NONE_ID = "none";
const NONE_OPTION: StyleOption = {
  id: NONE_ID,
  name: "不使用文风",
  description: "完全按预设 / 人设原文组装，不追加任何文风约束",
  tags: [],
  examples: 0,
};

export function StyleSwitcher({
  value,
  onChange,
  catalog,
  disabled,
}: {
  value: string;
  onChange: (id: string) => void;
  /** 后端文风清单；null = 尚未取回（不渲染，避免空选择器） */
  catalog: StyleCatalog | null;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const optionRefs = useRef<Array<HTMLLIElement | null>>([]);
  /** 仅当用键盘打开时才把焦点移入选项（鼠标点击时焦点留在触发器上更自然） */
  const focusOnOpenRef = useRef(false);

  const styles = useMemo(
    () => [NONE_OPTION, ...(catalog?.styles ?? [])],
    [catalog],
  );
  const current = styles.find((style) => style.id === value) ?? styles[0] ?? null;
  const selectedIndex = Math.max(
    0,
    styles.findIndex((style) => style.id === value),
  );

  /** 在选项之间移动焦点（首尾循环） */
  function focusOption(index: number) {
    const options = optionRefs.current.filter(
      (node): node is HTMLLIElement => node !== null,
    );
    if (options.length === 0) return;
    const next = ((index % options.length) + options.length) % options.length;
    options[next]?.focus();
  }

  /** 关闭列表；`returnFocus` 为真时把焦点还给触发器（键盘用户不丢位置） */
  function close(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }

  function select(id: string) {
    onChange(id);
    close(true);
  }

  // 键盘打开后，把焦点直接放到当前选中项
  useEffect(() => {
    if (!open || !focusOnOpenRef.current) return;
    focusOnOpenRef.current = false;
    focusOption(selectedIndex);
    // selectedIndex 入依赖是安全的：focusOnOpenRef 守卫保证「打开瞬间只跑一次」，
    // 之后即使 selectedIndex 变化也会在守卫处提前返回
  }, [open, selectedIndex]);

  // Esc 关闭（归还焦点）+ 点击外部关闭
  useEffect(() => {
    if (!open) return;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      close(true);
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target) return;
      if (triggerRef.current?.contains(target) || listRef.current?.contains(target)) return;
      close(false);
    }

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [open]);

  function handleTriggerKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    if (!open) {
      focusOnOpenRef.current = true;
      setOpen(true);
      return;
    }
    focusOption(selectedIndex);
  }

  function handleOptionKeyDown(event: ReactKeyboardEvent<HTMLLIElement>, index: number) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusOption(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusOption(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusOption(0);
    } else if (event.key === "End") {
      event.preventDefault();
      focusOption(styles.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(styles[index].id);
    }
  }

  // 后端未连接时不渲染（离线时对话本身也跑不起来，留着选择器只会误导；
  // `current` 的判空同时负责类型收窄）
  if (!catalog || !current) return null;

  return (
    <section aria-labelledby="style-title" className="relative">
      <h2 id="style-title" className="sr-only">
        文风
      </h2>

      <button
        ref={triggerRef}
        id={TRIGGER_ID}
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? LIST_ID : undefined}
        onClick={() => setOpen((prev) => !prev)}
        onKeyDown={handleTriggerKeyDown}
        className="focus-ring flex w-full items-center justify-between rounded-xl border border-line bg-surface-panel px-4 py-2.5 text-left text-sm text-ink transition-colors hover:border-accent/50 disabled:opacity-50"
      >
        <span>
          <span className="text-xs text-ink-soft">文风 · </span>
          {current.name}
        </span>
        <span className="text-xs text-ink-soft">{open ? "收起" : "切换"}</span>
      </button>

      {open && (
        <ul
          ref={listRef}
          id={LIST_ID}
          role="listbox"
          aria-labelledby={TRIGGER_ID}
          className="absolute z-20 mt-1 w-full overflow-hidden rounded-xl border border-line bg-surface-panel shadow-lg"
        >
          {styles.map((style, index) => {
            const selected = style.id === value;
            return (
              <li
                key={style.id}
                ref={(node) => {
                  optionRefs.current[index] = node;
                }}
                role="option"
                aria-selected={selected}
                tabIndex={-1}
                onClick={() => select(style.id)}
                onKeyDown={(event) => handleOptionKeyDown(event, index)}
                className={`focus-ring cursor-pointer px-4 py-2.5 text-left text-sm transition-colors hover:bg-surface-hover ${
                  selected ? "text-accent-text" : "text-ink-muted"
                }`}
              >
                <span className="block">{style.name}</span>
                <span className="block text-xs text-ink-soft">{style.description}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
