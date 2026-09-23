"use client";

/**
 * 情绪指示器：情绪标签 + 强度条 + 语气（来自 M4 情绪链路 / M5 播报元数据）。
 *
 * 职责只限「情绪」：召回条数等记忆证据已移到 MemoryTrace（见该组件说明），
 * 避免一张卡片混装两类信息、数字重复展示。
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

export function MoodIndicator({
  emotion,
  tone,
}: {
  emotion: EmotionInfo | null;
  tone?: string;
}) {
  if (!emotion) return null;
  const barColor = MOOD_BAR[emotion.label] ?? MOOD_BAR.neutral;
  // 强度容错：字段缺失或非数值时按 0 处理（否则会渲染出 NaN%）
  const intensity = Number.isFinite(emotion.intensity) ? emotion.intensity : 0;
  const percent = Math.round(Math.max(0, Math.min(1, intensity)) * 100);

  return (
    <section
      aria-labelledby="mood-title"
      className="rounded-xl border border-line bg-surface-panel px-4 py-3"
    >
      <div className="flex items-center justify-between text-xs text-ink-soft">
        {/* 用真实 h2 作区域标题：读屏可按标题/区域跳转（审计 A10） */}
        <h2 id="mood-title" className="text-xs text-ink-soft">
          当前情绪
        </h2>
        <span>{emotion.source === "llm" ? "模型识别" : "规则兜底"}</span>
      </div>

      <div className="mt-2 flex items-center gap-3">
        <span className="text-base text-ink">{emotion.label_zh}</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-hover">
          <div
            className={`h-full rounded-full transition-[width] duration-700 ${barColor}`}
            style={{ width: `${percent}%` }}
          />
        </div>
        <span className="text-xs tabular-nums text-ink-soft">{percent}%</span>
      </div>

      <div className="mt-2 flex flex-wrap gap-3 text-xs text-ink-soft">
        {tone && <span>语气：{tone}</span>}
        <span>
          表情键：<span translate="no">{emotion.facial_expression}</span>
        </span>
      </div>
    </section>
  );
}
