"use client";

/**
 * 字幕条：显示当前播报文本（消费后端的 speak.display_text）。
 *
 * 无障碍要点（审计 A5 / C3）：字幕是语音播报的**等价文本**，
 * 因此外层是 live region（`role="status"`）——变化时读屏会播报；
 * 长句用 `line-clamp-3` + `break-words` 收敛，避免把左栏撑高挤压数字人舞台。
 */

export function SubtitleBar({ text, active }: { text: string; active: boolean }) {
  if (!text) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={`line-clamp-3 break-words rounded-xl border px-4 py-3 text-center text-sm leading-relaxed transition-colors ${
        active
          ? "border-success/30 bg-success-soft text-ink"
          : "border-line bg-surface-raised text-ink-muted"
      }`}
    >
      {text}
    </div>
  );
}
