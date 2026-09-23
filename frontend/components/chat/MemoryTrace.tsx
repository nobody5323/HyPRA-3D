"use client";

/**
 * 本轮记忆与行动轨迹（可解释性面板）。
 *
 * 存在的理由：后端把「这一轮到底动用了什么」全部如实返回，但此前界面上只有
 * 一个「召回记忆 N 条」的合计数字——记忆分层、世界书触发、后台写入这些
 * **评分点上的能力在演示时看不见**。本组件把它们摊开显示。
 *
 * 数据来源：POST /chat 响应的 memory_counts / knowledge_hits / worldbook_hits /
 * memory_scheduled / estimated_tokens / warnings（顺序与后端 PromptManager 的
 * 注入优先级一致：世界书 > 个人记忆 > 情景记忆 > 语义事实）。
 *
 * 首轮对话之前（无任何数据）不渲染，避免空面板占位。
 */

import type { MemoryCounts } from "@/lib/api/types";

export function MemoryTrace({
  memoryCounts,
  knowledgeHits,
  worldbookHits,
  memoryScheduled,
  estimatedTokens,
  warnings,
}: {
  memoryCounts: MemoryCounts;
  /** 个人记忆（知识库）召回条数 */
  knowledgeHits: number;
  /** 命中的世界书条目 id */
  worldbookHits: string[];
  /** 本轮记忆写入已提交后台 */
  memoryScheduled: boolean;
  /** 系统提示词估算 token */
  estimatedTokens: number;
  warnings: string[];
}) {
  const memories = memoryCounts.memories ?? 0;
  const facts = memoryCounts.facts ?? 0;

  const isEmpty =
    memories === 0 &&
    facts === 0 &&
    knowledgeHits === 0 &&
    worldbookHits.length === 0 &&
    warnings.length === 0;
  if (isEmpty) return null;

  return (
    <section
      aria-labelledby="memory-trace-title"
      className="rounded-xl border border-line bg-surface-panel px-4 py-3"
    >
      <div className="flex items-center justify-between text-xs text-ink-soft">
        <h2 id="memory-trace-title" className="text-xs text-ink-soft">
          本轮记忆与行动
        </h2>
        {memoryScheduled && <span className="text-success-text">已后台写入记忆</span>}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {worldbookHits.length > 0 && (
          <span className="text-ink-muted">
            世界书触发：
            <span translate="no" className="text-brand-text">
              {worldbookHits.join("、")}
            </span>
          </span>
        )}
        {knowledgeHits > 0 && (
          <span className="text-ink-muted">
            个人记忆 <span className="tabular-nums text-accent-text">{knowledgeHits}</span> 条
          </span>
        )}
        {memories > 0 && (
          <span className="text-ink-muted">
            情景记忆 <span className="tabular-nums text-accent-text">{memories}</span> 条
          </span>
        )}
        {facts > 0 && (
          <span className="text-ink-muted">
            语义事实 <span className="tabular-nums text-accent-text">{facts}</span> 条
          </span>
        )}
        {estimatedTokens > 0 && (
          <span className="tabular-nums text-ink-faint">提示词 ≈{estimatedTokens} tokens</span>
        )}
      </div>

      {warnings.length > 0 && (
        <ul role="alert" className="mt-2 space-y-1 text-xs leading-relaxed text-warning-text">
          {warnings.map((warning) => (
            <li key={warning} className="break-words">
              {warning}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
