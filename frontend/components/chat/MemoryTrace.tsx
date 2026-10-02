"use client";

/**
 * 本轮记忆与行动轨迹（可解释性面板）。
 *
 * 存在的理由：后端把「这一轮到底动用了什么」全部如实返回，但此前界面上只有
 * 一个「召回记忆 N 条」的合计数字——记忆分层、世界书触发、后台写入这些
 * **评分点上的能力在演示时看不见**。本组件把它们摊开显示。
 *
 * 数据来源：POST /chat 响应的 mode / memory_counts / knowledge_hits /
 * worldbook_hits / memory_scheduled / estimated_tokens / warnings
 * （顺序与后端 PromptManager 的注入优先级一致：
 * 世界书 > 个人记忆 > 情景记忆 > 语义事实）。
 *
 * 首轮对话之前（无任何数据）不渲染，避免空面板占位。
 *
 * **模式为什么要显示**：酒馆世界书只在酒馆聊天模式下召回（`app/memory/knowledge/scopes.py`）。
 * 用户在桌宠模式下问酒馆设定却什么都召不回时，「模式」是唯一能解释这件事的字段
 * ——此前它不在界面上，只能翻后端留痕，实测被读成「召回坏了」。
 */

import type { MemoryCounts } from "@/lib/api/types";
import { MODE_TAVERN, modeLabel } from "@/lib/chat/mode";

export function MemoryTrace({
  mode,
  memoryCounts,
  knowledgeHits,
  worldbookHits,
  memoryScheduled,
  estimatedTokens,
  warnings,
}: {
  /** 本轮实际生效的交互模式（空串 = 未聊过 / 老后端没返回） */
  mode: string;
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
  const modeText = modeLabel(mode);

  const isEmpty =
    memories === 0 &&
    facts === 0 &&
    knowledgeHits === 0 &&
    worldbookHits.length === 0 &&
    warnings.length === 0 &&
    // 桌宠模式 + 什么都没召回 → 面板整块不渲染（默认档不该有常驻噪声）；
    // 酒馆模式则一定要露出来：它是「酒馆世界书为什么在这轮生效」的唯一线索。
    mode !== MODE_TAVERN;
  if (isEmpty) return null;

  return (
    <section
      aria-labelledby="memory-trace-title"
      className="card shrink-0 animate-fade-up px-4 py-3"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id="memory-trace-title" className="flex items-center gap-2 text-xs font-medium text-ink-muted">
          <span aria-hidden="true" className="status-dot h-2 w-2 bg-accent" />
          本轮记忆与行动
        </h2>
        {memoryScheduled && (
          <span className="chip chip-success">
            <span aria-hidden="true" className="status-dot animate-pulse-dot" />
            已后台写入记忆
          </span>
        )}
      </div>

      {/*
        命中项用芯片铺开。
        注意：数字必须是**独立的文本节点**（`<span>3</span>`），
        否则既不利于读屏逐项朗读，也让「数字」难以被单独定位。
      */}
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {modeText && (
          <span className="chip chip-neutral">
            模式：
            <span
              translate="no"
              className={mode === MODE_TAVERN ? "font-medium text-brand-text" : "text-ink-muted"}
            >
              {modeText}
            </span>
          </span>
        )}
        {worldbookHits.length > 0 && (
          <span className="chip chip-brand">
            世界书触发：
            <span translate="no">{worldbookHits.join("、")}</span>
          </span>
        )}
        {knowledgeHits > 0 && (
          // 单行书写：JSX 会吃掉换行处的空白，拆成多行会让「3」与「条」黏在一起
          <span className="chip chip-accent">
            个人记忆 <span className="tabular-nums font-semibold">{knowledgeHits}</span> 条
          </span>
        )}
        {memories > 0 && (
          <span className="chip chip-accent">
            情景记忆 <span className="tabular-nums font-semibold">{memories}</span> 条
          </span>
        )}
        {facts > 0 && (
          <span className="chip chip-accent">
            语义事实 <span className="tabular-nums font-semibold">{facts}</span> 条
          </span>
        )}
        {estimatedTokens > 0 && (
          <span className="chip chip-neutral tabular-nums">提示词 ≈{estimatedTokens} tokens</span>
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
