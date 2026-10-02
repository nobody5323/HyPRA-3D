"use client";

/**
 * 模型预设切换器。
 *
 * 用途：不同模型对采样参数的容忍度与推理链默认行为不同（如推理模型会先生成
 * 上千思考 token，情感陪伴场景慢约 6 倍），因此按模型分档配置。
 *
 * 「自动」= 后端按当前模型名匹配（见 GET /chat/presets 的 auto_preset_id）；
 * 也可手动指定某一档，下一轮对话即生效。
 *
 * 可访问性：与文风切换器同款 listbox 语义（aria-expanded/haspopup/controls、
 * ↑↓ 移动、Escape 关闭并归还焦点、点击面板外部关闭）。
 */

import { useEffect, useId, useRef, useState } from "react";

import type { PresetCatalog, PresetInfo } from "@/lib/api/types";

import { useAnchoredPopup } from "./useAnchoredPopup";

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

/** 采样参数摘要（界面上一眼看清这一档在做什么） */
function describePreset(preset: PresetInfo): string {
  const parts = [`temp ${preset.temperature}`];
  if (preset.top_p !== null) parts.push(`top_p ${preset.top_p}`);
  if (preset.max_tokens !== null) parts.push(`≤${preset.max_tokens} 字`);
  if (preset.enable_thinking === true) parts.push("思考开");
  if (preset.enable_thinking === false) parts.push("思考关");
  return parts.join(" · ");
}

export function PresetSwitcher({
  value,
  onChange,
  catalog,
  disabled,
}: {
  /** 当前选定的预设 id；"" = 自动（按模型名匹配） */
  value: string;
  onChange: (id: string) => void;
  catalog: PresetCatalog | null;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const listboxId = useId();
  /**
   * 浮层坐标。**必须是 `fixed`**：面板会被控制台的 `Card overflow-hidden` 包住，
   * `absolute` 浮层会被裁成一条缝（详见 `useAnchoredPopup` 的文件头）。
   */
  const popupStyle = useAnchoredPopup(triggerRef, open);

  const autoId = catalog?.auto_preset_id ?? "";
  const autoLabel =
    catalog?.presets.find((preset) => preset.id === autoId)?.label ?? autoId ?? "未知";
  const current = catalog?.presets.find((preset) => preset.id === value) ?? null;
  const currentLabel = current?.label ?? `自动（${autoLabel}）`;

  // 选项 = 自动档 + 各预设档
  const options: { id: string; label: string; hint: string; selected: boolean }[] = [
    {
      id: "",
      label: "自动（按模型匹配）",
      hint: catalog ? `当前模型 ${catalog.model} → ${autoLabel}` : "后端未连接",
      selected: value === "",
    },
    ...(catalog?.presets ?? []).map((preset) => ({
      id: preset.id,
      label: preset.label,
      hint: preset.description || describePreset(preset),
      selected: value === preset.id,
    })),
  ];

  useEffect(() => {
    if (!open) return;
    const selectedIndex = Math.max(
      0,
      options.findIndex((option) => option.selected),
    );
    optionRefs.current[selectedIndex]?.focus();

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
    // options 由 catalog/value 派生，随二者变化重新定位焦点即可
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, value, catalog]);

  const closeAndRestoreFocus = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const moveFocus = (from: number, delta: number) => {
    const count = options.length;
    optionRefs.current[(from + delta + count) % count]?.focus();
  };

  if (!catalog) return null; // 后端不可用时不渲染（避免空选择器）

  return (
    /*
     * 不需要 `relative`：浮层走 `fixed`（见 popupStyle），锚点由 hook 算。
     * `rootRef` 仍要留着——「点击外部关闭」用它做命中判定（浮层没有搬进 portal，
     * 仍是它的 DOM 后代）。
     */
    <div ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={listboxId}
        onClick={() => setOpen((prev) => !prev)}
        className={`flex w-full items-center justify-between card px-4 py-2.5 text-left text-sm text-ink transition-colors hover:border-accent/50 disabled:opacity-50 ${FOCUS_RING}`}
      >
        <span>
          <span className="text-xs text-ink-soft">预设 · </span>
          {currentLabel}
        </span>
        <span className="text-xs text-ink-soft" aria-hidden="true">
          {open ? "收起" : "切换"}
        </span>
      </button>

      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-label="选择模型预设"
          style={popupStyle}
          className="z-20 overflow-y-auto rounded-2xl border border-line bg-surface-panel shadow-float"
        >
          {options.map((option, index) => (
            <button
              key={option.id || "auto"}
              ref={(node) => {
                optionRefs.current[index] = node;
              }}
              type="button"
              role="option"
              aria-selected={option.selected}
              tabIndex={-1}
              onClick={() => {
                onChange(option.id);
                closeAndRestoreFocus();
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  closeAndRestoreFocus();
                } else if (event.key === "ArrowDown") {
                  event.preventDefault();
                  moveFocus(index, 1);
                } else if (event.key === "ArrowUp") {
                  event.preventDefault();
                  moveFocus(index, -1);
                }
              }}
              className={`block w-full px-4 py-2.5 text-left text-sm transition-colors hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent ${
                option.selected ? "text-accent-text" : "text-ink-muted"
              }`}
            >
              <span className="block">
                {option.label}
                {option.selected && <span className="ml-2 text-xs text-ink-soft">当前</span>}
              </span>
              <span className="block break-words text-xs text-ink-soft">{option.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
