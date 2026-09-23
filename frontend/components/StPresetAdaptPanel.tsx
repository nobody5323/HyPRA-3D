"use client";

/**
 * AI 适配面板：把导入的酒馆预设一键改造成本项目的陪伴对话形态。
 *
 * 交互契约（对应后端 `POST /chat/st-presets/{id}/ai-adapt`）：
 * 1. 先**试运行**拿建议（dry_run=true）—— 不动覆盖层，用户能看清要改什么；
 * 2. 用户点「应用这些修改」时，把**刚才那份补丁原样**写入覆盖层（走 PATCH），
 *    不重跑适配 —— 否则第二次模型输出可能不同，落盘的就不是用户确认过的内容；
 * 3. 原始导入文件始终只读，面板外随时可「恢复导入时」整体回滚。
 *
 * 为什么不做自动应用：适配会改动用户自己导入的提示词。静默改写好坏都得让人看一眼——
 * 这也是「宁可不做，不可做坏」在本项目的一贯口径。
 *
 * 合规（AGENTS.md §6）：这里展示的正文片段来自**用户自己导入的文件**，
 * 只在本机浏览器 ↔ 本地后端之间流转，不进仓库、不进日志。
 */

import { useCallback, useState } from "react";

import { ApiError, adaptStPreset, patchStPreset } from "@/lib/api";
import { isEmptyPatch } from "@/lib/st-preset";
import type { StAdaptDiffEntry, StAdaptResult, StPresetDetail } from "@/lib/types";

/** 键盘焦点样式（与面板其余控件一致：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

const SECONDARY_BUTTON =
  "rounded-full bg-surface-raised px-2.5 py-0.5 text-xs text-ink-muted ring-1 ring-line transition-colors hover:bg-surface-hover disabled:opacity-40";

const PRIMARY_BUTTON =
  "rounded-full bg-accent px-3 py-1 text-xs text-ink-on transition-colors hover:bg-accent-hover disabled:opacity-50";

/** 严重度配色（复用项目已有的 warning / danger 语义色） */
const SEVERITY_STYLE: Record<string, string> = {
  high: "bg-danger-soft text-danger-text ring-danger/30",
  medium: "bg-warning-soft text-warning-text ring-warning/30",
  low: "bg-surface-raised text-ink-soft ring-line",
};

const SEVERITY_LABEL: Record<string, string> = {
  high: "高危",
  medium: "中等",
  low: "轻微",
};

/** 处置动作的展示名（让用户看懂「这条是被自动修了，还是留给模型改」） */
const ACTION_LABEL: Record<string, string> = {
  auto_patch: "已自动修",
  llm_rewrite: "交给模型改",
  preserve: "内容保留",
  review: "仅提示",
};

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  return err instanceof Error ? err.message : fallback;
}

/** 把前后值渲染成一行短文本（正文只给字数，不给全文——否则一屏塞不下） */
function describeValue(field: string, value: unknown): string {
  if (value === null || value === undefined) return "（未设置）";
  if (field === "content") return `${String(value).length} 字`;
  if (typeof value === "boolean") return value ? "开" : "关";
  if (typeof value === "number") return String(value);
  const text = String(value);
  return text.length > 48 ? `${text.slice(0, 48)}…` : text;
}

function DiffRow({ entry }: { entry: StAdaptDiffEntry }) {
  const isOrder = entry.scope === "order";
  const title = isOrder
    ? "注入顺序"
    : entry.scope === "preset"
      ? entry.field_label
      : `${entry.name} · ${entry.field_label}`;

  return (
    <li className="rounded-lg bg-surface-panel px-2.5 py-1.5 text-xs text-ink-muted ring-1 ring-line">
      <span className="text-ink-soft">{title}</span>
      {isOrder ? (
        <ul className="mt-1 space-y-0.5">
          {(entry.moved ?? []).map((move) => (
            <li key={move.identifier} className="text-ink-faint">
              {move.name}：第 {move.from_index + 1} 位 → 第 {move.to_index + 1} 位
            </li>
          ))}
        </ul>
      ) : (
        <span className="ml-2">
          <span className="text-ink-faint">{describeValue(entry.field, entry.before)}</span>
          <span aria-hidden="true" className="mx-1 text-ink-faint">
            →
          </span>
          <span className="text-ink">{describeValue(entry.field, entry.after)}</span>
        </span>
      )}
    </li>
  );
}

export function StPresetAdaptPanel({
  presetId,
  disabled = false,
  onApplied,
}: {
  presetId: string;
  /** 对话进行中禁止改动预设（与编辑器同一口径） */
  disabled?: boolean;
  /** 适配落盘后回传最新详情，面板据此刷新编辑器 */
  onApplied: (detail: StPresetDetail) => void;
}) {
  const [planState, setPlanState] = useState<{
    presetId: string;
    result: StAdaptResult;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  // 只认**当前预设**的建议：父组件切换预设时马上失效，避免把 A 的对照
  // 当成 B 的结果、又把改动写到 B 上（切换后 detail 会更新，但本组件不会重挂载）。
  const plan = planState && planState.presetId === presetId ? planState.result : null;

  const analyze = useCallback(async () => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      // 始终先试运行：应用与否由用户看完对照再决定
      setPlanState({ presetId, result: await adaptStPreset(presetId, { dryRun: true }) });
    } catch (err: unknown) {
      setError(errorText(err, "AI 适配失败"));
    } finally {
      setBusy(false);
    }
  }, [presetId]);

  const apply = useCallback(async () => {
    if (!plan) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      // 直接写入**用户刚确认的**那份补丁，而不是重跑一次适配：
      // 重跑会有第二次模型输出，落盘的可能就不是用户在对照里看到的内容了。
      const updated = await patchStPreset(presetId, plan.patch);
      setPlanState(null);
      onApplied(updated);
      setStatus(
        `已应用适配改动${plan.model_used ? "（含模型改写）" : "（仅确定性修复）"}`,
      );
    } catch (err: unknown) {
      setError(errorText(err, "应用适配改动失败"));
    } finally {
      setBusy(false);
    }
  }, [onApplied, plan, presetId]);

  const problemCount = plan
    ? plan.summary.high + plan.summary.medium + plan.summary.low
    : 0;
  const appliedRewrites = plan?.rewrites.filter((item) => item.status === "applied") ?? [];

  return (
    <div className="rounded-xl border border-line bg-surface-inset px-3 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm text-ink">AI 适配</p>
          <p className="text-xs text-ink-soft">
            把导入的预设改造成陪伴对话形态：人称、输出格式、回复长度、推理开关
          </p>
        </div>
        <button
          type="button"
          disabled={disabled || busy}
          onClick={() => void analyze()}
          className={`${SECONDARY_BUTTON} ${FOCUS_RING}`}
        >
          {busy && !plan ? "分析中…" : "先看建议"}
        </button>
      </div>

      {error && (
        <p role="alert" className="mt-2 text-xs text-danger-text">
          {error}
        </p>
      )}
      {status && (
        <p role="status" className="mt-2 text-xs text-ink-soft">
          {status}
        </p>
      )}

      {plan && (
        <div className="mt-3 space-y-3">
          <p className="text-xs text-ink-muted">
            发现 <span className="text-ink">{problemCount}</span> 个问题
            {problemCount > 0 && (
              <span className="text-ink-faint">
                （高危 {plan.summary.high} / 中等 {plan.summary.medium} / 轻微{" "}
                {plan.summary.low}）
              </span>
            )}
            {!plan.model_used && (
              <span className="text-warning-text">，模型未参与，仅包含确定性修复</span>
            )}
          </p>

          {plan.diff.length > 0 ? (
            <ul className="space-y-1">
              {plan.diff.map((entry) => (
                <DiffRow
                  key={`${entry.scope}:${entry.identifier}:${entry.field}`}
                  entry={entry}
                />
              ))}
            </ul>
          ) : (
            <p className="text-xs text-ink-soft">没有需要改动的地方。</p>
          )}

          {plan.preserved.length > 0 && (
            <p className="text-xs text-ink-faint">
              以下 {plan.preserved.length} 条内容保持原样，只调整注入位置：
              {plan.preserved.map((item) => item.name).join("、")}
            </p>
          )}

          {appliedRewrites.length > 0 && (
            <details className="text-xs text-ink-soft">
              <summary className="cursor-pointer">
                模型改写了 {appliedRewrites.length} 条的正文（点开看明细）
              </summary>
              <ul className="mt-1 space-y-1">
                {appliedRewrites.map((item) => (
                  <li key={item.identifier} className="text-ink-faint">
                    {item.name}：{item.reason || "已改写"}
                  </li>
                ))}
              </ul>
            </details>
          )}

          {plan.findings.length > 0 && (
            <details className="text-xs text-ink-soft">
              <summary className="cursor-pointer">
                体检发现 {plan.findings.length} 条（点开看明细）
              </summary>
              <ul className="mt-1 space-y-1">
                {plan.findings.map((finding, index) => (
                  <li
                    key={`${finding.rule_id}:${finding.identifier}:${index}`}
                    className="flex flex-wrap items-center gap-1"
                  >
                    <span
                      className={`rounded-full px-1.5 py-0.5 ring-1 ${
                        SEVERITY_STYLE[finding.severity] ?? SEVERITY_STYLE.low
                      }`}
                    >
                      {SEVERITY_LABEL[finding.severity] ?? finding.severity}
                    </span>
                    <span className="rounded-full bg-surface-raised px-1.5 py-0.5 text-ink-faint ring-1 ring-line">
                      {ACTION_LABEL[finding.action] ?? finding.action}
                    </span>
                    <span className="text-ink-muted">{finding.name || "预设设置"}</span>
                    <span className="text-ink-faint">{finding.message}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {plan.warnings.length > 0 && (
            <details className="text-xs text-ink-soft">
              <summary className="cursor-pointer">
                适配提示 {plan.warnings.length} 条
              </summary>
              <ul className="mt-1 space-y-0.5">
                {plan.warnings.map((warning, index) => (
                  <li key={index} className="text-ink-faint">
                    {warning}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={disabled || busy || isEmptyPatch(plan.patch)}
              onClick={() => void apply()}
              className={`${PRIMARY_BUTTON} ${FOCUS_RING}`}
            >
              {busy ? "应用中…" : "应用这些修改"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setPlanState(null)}
              className={`${SECONDARY_BUTTON} ${FOCUS_RING}`}
            >
              取消
            </button>
            <span className="text-xs text-ink-faint">
              原始文件不会被改写，随时可在列表里「恢复导入时」
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
