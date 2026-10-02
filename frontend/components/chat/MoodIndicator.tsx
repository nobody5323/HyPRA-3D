"use client";

/**
 * 情绪指示器：情绪标签 + 强度条 + 语气（来自 M4 情绪链路 / M5 播报元数据）。
 *
 * 职责只限「情绪」：召回条数等记忆证据已移到 MemoryTrace（见该组件说明），
 * 避免一张卡片混装两类信息、数字重复展示。
 *
 * 视觉上它是**整页唯一会「变色」的卡片**：左侧那颗点与强度条同色（取自 mood 色板），
 * 换情绪时颜色与宽度一起过渡——演示时不用读文字也能看出情绪在动。
 */

import type { EmotionInfo } from "@/lib/api/types";

/** 情绪强度条颜色：统一取自 tailwind.config.ts 的 mood 色板（全项目唯一来源） */
const MOOD_BAR: Record<string, string> = {
  happy: "bg-mood-happy",
  calm: "bg-mood-calm",
  sad: "bg-mood-sad",
  anxious: "bg-mood-anxious",
  tired: "bg-mood-tired",
  angry: "bg-mood-angry",
  surprised: "bg-mood-surprised",
  neutral: "bg-mood-neutral",
};

/** 情绪色 hex（与 tailwind.config.ts 的 mood.* 一致）：用于圆点与外环 */
const MOOD_HEX: Record<string, string> = {
  happy: "#E0A24E",
  calm: "#6E9BB5",
  sad: "#7E8FA8",
  anxious: "#D08B5E",
  tired: "#8B87A8",
  angry: "#C4706A",
  surprised: "#D4A94F",
  neutral: "#9A948C",
};

export function MoodIndicator({
  emotion,
  tone,
}: {
  emotion: EmotionInfo | null;
  tone?: string;
}) {
  if (!emotion) return null;
  const barColor = MOOD_BAR[emotion.label] ?? MOOD_BAR.neutral;
  const hex = MOOD_HEX[emotion.label] ?? MOOD_HEX.neutral;
  // 强度容错：字段缺失或非数值时按 0 处理（否则会渲染出 NaN%）
  const intensity = Number.isFinite(emotion.intensity) ? emotion.intensity : 0;
  const percent = Math.round(Math.max(0, Math.min(1, intensity)) * 100);

  return (
    <section
      aria-labelledby="mood-title"
      className="card shrink-0 animate-fade-up px-4 py-3"
    >
      <div className="flex items-center justify-between gap-3">
        {/* 用真实 h2 作区域标题：读屏可按标题/区域跳转（审计 A10） */}
        <h2 id="mood-title" className="flex items-center gap-2 text-xs font-medium text-ink-muted">
          <span
            aria-hidden="true"
            className="status-dot h-2 w-2 transition-colors duration-500"
            style={{ backgroundColor: hex, boxShadow: `0 0 0 3px ${hex}22` }}
          />
          当前情绪
        </h2>
        <span className="chip chip-neutral">
          {emotion.source === "llm" ? "模型识别" : "规则兜底"}
        </span>
      </div>

      <div className="mt-2.5 flex items-center gap-3">
        <span
          translate="no"
          className="shrink-0 text-base font-semibold tracking-tight transition-colors duration-500"
          style={{ color: hex }}
        >
          {emotion.label_zh}
        </span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-surface-hover">
          <div
            className={`h-full rounded-full transition-[width] duration-700 ease-out ${barColor}`}
            style={{ width: `${percent}%` }}
          />
        </div>
        <span className="shrink-0 text-xs tabular-nums text-ink-muted">{percent}%</span>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-soft">
        {tone && (
          <span>
            语气 <span className="text-ink-muted">{tone}</span>
          </span>
        )}
        <span>
          表情键{" "}
          <span translate="no" className="text-ink-muted">
            {emotion.facial_expression}
          </span>
        </span>
      </div>
    </section>
  );
}
