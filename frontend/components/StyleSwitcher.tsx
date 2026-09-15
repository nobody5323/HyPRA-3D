"use client";

/**
 * 文风切换器（A/B 对比演示用）。
 *
 * 四种预设与后端 `backend/app/prompts/style/presets/` 一一对应，
 * 切换后下一轮对话即生效（前端仅传 style_id）。
 *
 * 无障碍要点（审计 A2 / A10）：
 * - 触发器暴露 `aria-expanded` / `aria-haspopup="listbox"` / `aria-controls`；
 * - 弹出层是 `role="listbox"`，每项是 `role="option"` + `aria-selected`；
 * - 键盘完整可用：Enter/Space 打开、↑↓ 移动、Home/End 首尾、Esc 关闭并归还焦点、点击外部关闭。
 */

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

const STYLES: { id: string; name: string; hint: string }[] = [
  { id: "modern-conversational", name: "现代口语", hint: "像熟人聊天，短句 + 语气词" },
  { id: "brief-direct", name: "简短利落", hint: "一两句，留白" },
  { id: "classical-elegant", name: "古典雅致", hint: "含蓄，带一点文气" },
  { id: "gentle-elaborate", name: "细腻长句", hint: "有动作神态描写" },
];

const TRIGGER_ID = "style-trigger";
const LIST_ID = "style-listbox";

export function StyleSwitcher({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const optionRefs = useRef<Array<HTMLLIElement | null>>([]);
  /** 仅当用键盘打开时才把焦点移入选项（鼠标点击时焦点留在触发器上更自然） */
  const focusOnOpenRef = useRef(false);

  const current = STYLES.find((style) => style.id === value) ?? STYLES[0];
  const selectedIndex = Math.max(
    0,
    STYLES.findIndex((style) => style.id === value),
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      focusOption(STYLES.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(STYLES[index].id);
    }
  }

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
          {STYLES.map((style, index) => {
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
                <span className="block text-xs text-ink-soft">{style.hint}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
