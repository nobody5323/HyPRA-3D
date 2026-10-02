"use client";

/**
 * 字幕条：显示当前播报文本（消费后端的 speak.display_text）。
 *
 * 无障碍要点（审计 A5 / C3）：字幕是语音播报的**等价文本**，
 * 因此外层是 live region（`role="status"`）——变化时读屏会播报；
 * 长句用 `line-clamp-3` + `break-words` 收敛，避免把左栏撑高挤压数字人舞台。
 *
 * 视觉：播报中左侧那颗点会呼吸并转成成功色，静默时是中性色——
 * 「现在有没有在说话」不看文字也能看出来。
 */

export function SubtitleBar({ text, active }: { text: string; active: boolean }) {
  if (!text) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className={`flex shrink-0 items-start gap-2.5 rounded-xl border px-4 py-3 text-left text-sm leading-relaxed transition-colors duration-300 ${
        active
          ? "border-success/30 bg-success-soft text-ink shadow-card"
          : "border-line bg-surface-raised text-ink-muted"
      }`}
    >
      <span
        aria-hidden="true"
        className={`mt-1.5 shrink-0 rounded-full transition-all ${
          active ? "h-2 w-2 animate-pulse-dot bg-success" : "h-1.5 w-1.5 bg-ink-faint"
        }`}
      />
      <p className="line-clamp-3 min-w-0 break-words">{text}</p>
    </div>
  );
}
