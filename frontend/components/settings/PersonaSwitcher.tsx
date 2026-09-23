"use client";

/**
 * 陪伴对象（人设）切换器。
 *
 * 人设 id 同时是**记忆隔离命名空间**（后端的 `companion_id`）：情景记忆、
 * 语义事实、个人记忆都按它分库，所以切换人设 = 换一整套记忆 + 新的会话
 * （见 useChatSession 的 changePersona）。这一点必须让用户看得见，否则会
 * 误以为「换角色后还记得我」。
 *
 * 清单来自 `GET /chat/personas`；只有一个人设时不渲染（无切换价值）。
 *
 * 可访问性：与文风/模型预设切换器同款 listbox 语义
 * （aria-expanded / haspopup / controls、↑↓ 移动、Home/End、Escape 关闭并归还
 * 焦点、点击面板外部关闭）。
 */

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import type { PersonaCatalog } from "@/lib/api/types";

const TRIGGER_ID = "persona-trigger";
const LIST_ID = "persona-listbox";

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

export function PersonaSwitcher({
  value,
  onChange,
  catalog,
  disabled,
}: {
  value: string;
  onChange: (id: string) => void;
  /** 后端人设清单；null = 尚未取回 */
  catalog: PersonaCatalog | null;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  /** 整个弹出面板（含底部说明行）：用于「点击外部关闭」的命中判定 */
  const panelRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<Array<HTMLLIElement | null>>([]);
  /** 仅当用键盘打开时才把焦点移入选项（鼠标点击时焦点留在触发器上更自然） */
  const focusOnOpenRef = useRef(false);

  const personas = catalog?.personas ?? [];
  const current = personas.find((persona) => persona.id === value) ?? personas[0] ?? null;
  const selectedIndex = Math.max(
    0,
    personas.findIndex((persona) => persona.id === value),
  );

  /** 关闭列表；`returnFocus` 为真时把焦点还给触发器（键盘用户不丢位置） */
  function close(returnFocus: boolean) {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }

  function select(id: string) {
    onChange(id);
    close(true);
  }

  /** 在选项之间移动焦点（首尾循环） */
  function focusOption(index: number) {
    const options = optionRefs.current.filter(
      (node): node is HTMLLIElement => node !== null,
    );
    if (options.length === 0) return;
    const next = ((index % options.length) + options.length) % options.length;
    options[next]?.focus();
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
      if (triggerRef.current?.contains(target) || panelRef.current?.contains(target)) return;
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
      focusOption(personas.length - 1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(personas[index].id);
    }
  }

  // 只有一个人设时无切换价值（也不该用「只有一个选项的选择器」占位）
  if (!current || personas.length < 2) return null;

  return (
    <section aria-labelledby="persona-title" className="relative">
      <h2 id="persona-title" className="sr-only">
        陪伴对象
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
          <span className="text-xs text-ink-soft">陪伴对象 · </span>
          {current.name}
        </span>
        <span className="text-xs text-ink-soft">{open ? "收起" : "切换"}</span>
      </button>

      {open && (
        /* 说明行放在 listbox 之外：`role="listbox"` 的子元素只能是 option */
        <div
          ref={panelRef}
          className="absolute z-20 mt-1 w-full overflow-hidden rounded-xl border border-line bg-surface-panel shadow-lg"
        >
          <ul id={LIST_ID} role="listbox" aria-labelledby={TRIGGER_ID}>
            {personas.map((persona, index) => {
              const selected = persona.id === value;
              return (
                <li
                  key={persona.id}
                  ref={(node) => {
                    optionRefs.current[index] = node;
                  }}
                  role="option"
                  aria-selected={selected}
                  tabIndex={-1}
                  onClick={() => select(persona.id)}
                  onKeyDown={(event) => handleOptionKeyDown(event, index)}
                  className={`${FOCUS_RING} cursor-pointer px-4 py-2.5 text-left text-sm transition-colors hover:bg-surface-hover ${
                    selected ? "text-accent-text" : "text-ink-muted"
                  }`}
                >
                  <span className="block">{persona.name}</span>
                  <span className="block text-xs text-ink-soft">{persona.title}</span>
                </li>
              );
            })}
          </ul>
          <p className="border-t border-line px-4 py-2 text-xs leading-relaxed text-ink-faint">
            切换陪伴对象会开始一段新对话——记忆按对象独立存储，互不混用。
          </p>
        </div>
      )}
    </section>
  );
}
