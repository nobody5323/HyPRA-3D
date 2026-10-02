"use client";

/**
 * 「一句话需求 → 生成草稿」的共用小表单（`AGENTS.md §9.12`）。
 *
 * 人设 / 技能 / 插件三处都用它。抽出来的理由不是省几行，而是这三处的
 * **交互约定必须一致**：都是「描述需求 → 生成 → 审阅 → 显式保存」，
 * 复制三份之后必然分叉（placeholder 不同、busy 时能不能改、错误怎么显示）。
 *
 * 这里只负责「描述 + 触发生成」，草稿的展示与保存由各面板自己决定——
 * 三者的草稿形态差别太大（表单字段 / frontmatter / 代码），强行统一反而更难用。
 */

const FIELD_CLASS =
  "focus-ring w-full rounded-lg border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint disabled:opacity-60";

export function AiBriefForm({
  brief,
  onBriefChange,
  onGenerate,
  busy = false,
  disabled = false,
  label,
  placeholder,
  actionLabel = "AI 生成",
  hint,
  error,
  idPrefix,
}: {
  brief: string;
  onBriefChange: (value: string) => void;
  onGenerate: () => void;
  busy?: boolean;
  disabled?: boolean;
  label: string;
  placeholder: string;
  actionLabel?: string;
  /** 说明文字（告诉用户会生成什么、生成后会发生什么） */
  hint?: string;
  /** 生成失败的原因（原样展示：后端的提示已经写了该怎么办） */
  error?: string | null;
  idPrefix: string;
}) {
  const fieldId = `${idPrefix}-brief`;
  const hintId = `${fieldId}-hint`;
  const blocked = busy || disabled;

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-line bg-surface-inset p-3">
      <label htmlFor={fieldId} className="text-xs text-ink-muted">
        {label}
      </label>
      <textarea
        id={fieldId}
        value={brief}
        onChange={(event) => onBriefChange(event.target.value)}
        disabled={blocked}
        rows={2}
        aria-describedby={hint ? hintId : undefined}
        placeholder={placeholder}
        className={`${FIELD_CLASS} resize-y`}
      />
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onGenerate}
          disabled={blocked || brief.trim().length < 2}
          className="focus-ring rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint"
        >
          {busy ? "生成中…" : actionLabel}
        </button>
        {hint && (
          <span id={hintId} className="text-[11px] leading-relaxed text-ink-faint">
            {hint}
          </span>
        )}
      </div>
      {error && (
        <p role="alert" className="text-[11px] leading-relaxed text-danger-text">
          {error}
        </p>
      )}
    </div>
  );
}
