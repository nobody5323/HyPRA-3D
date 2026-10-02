import { useState } from "react";

import { SessionList } from "@/components/chat/SessionList";
import { KnowledgePanel } from "@/components/studio/KnowledgePanel";
import { deleteSession, deleteSessions, purgeMemory } from "@/lib/api/client";
import { personaName } from "@/lib/chat/persona";
import type { PersonaCatalog } from "@/lib/api/types";

import { usePersonaScope } from "../usePersonaScope";

/**
 * 会话与记忆分组。
 *
 * 三块：
 * - **会话列表**：浏览与删除（新建与「接着聊」在 Web / 桌宠界面里做——控制台没有对话面板，
 *   在这里造一个空会话只会得到一个点了没反应的入口）。删除请求由**本面板**发（见 `onRemove`）：
 *   `SessionList` 只是视图，它自己不调 API，Web / 桌宠那边是 `useChatSession` 在发；
 * - **个人记忆**：上传语料、查看分块（`KnowledgePanel` 本身自包含）；
 * - **记忆架构说明**：实时记忆追踪（`MemoryTrace`）是**对话过程**的可视化，
 *   脱离对话语境没有意义，所以这里不放它，改为写清四层记忆的分工。
 *
 * 陪伴对象由宿主**受控**（`ConsoleApp` 统一持有）：它同时供着人设与文风、
 * 酒馆记忆导入两处，各存一份必然漂移。不传时回落到共用 hook，单独用也能工作。
 */

/** 会话所属的陪伴对象（记忆作用域 `companion:{id}`） */
export function MemoryPanel({
  personaId: controlledPersonaId,
  personaCatalog,
  onPersonaChange,
}: {
  /** 受控的当前陪伴对象 id；不传则由本面板自管 */
  personaId?: string;
  /** 受控的人设清单；不传则由本面板自拉 */
  personaCatalog?: PersonaCatalog | null;
  /** 受控模式下切换陪伴对象的回调 */
  onPersonaChange?: (id: string) => void;
} = {}) {
  const scope = usePersonaScope();
  const personaId = controlledPersonaId ?? scope.personaId;
  const catalog = personaCatalog ?? scope.catalog;
  const select = (id: string): void => {
    if (onPersonaChange) {
      onPersonaChange(id);
      return;
    }
    void scope.select(id);
  };
  /** 列表刷新版本（删除会话后自增） */
  const [version, setVersion] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  /** 正在二次确认「清空该角色的全部记忆」 */
  const [pendingPurge, setPendingPurge] = useState(false);
  const [purging, setPurging] = useState(false);

  /** 清空该角色的全部记忆（已在界面上过了二次确认） */
  async function confirmPurge() {
    setPendingPurge(false);
    setPurging(true);
    try {
      const result = await purgeMemory(personaId);
      const layers = [
        `情景记忆 ${result.removed.warm ?? 0}`,
        `语义事实 ${result.removed.facts ?? 0}`,
        `情绪日记 ${result.removed.mood_log ?? 0}`,
        `个人记忆分块 ${result.removed.knowledge ?? 0}`,
      ];
      setNotice(
        `已清空「${personaName(catalog, personaId)}」的记忆：${layers.join("、")}。会话记录未受影响。`,
      );
    } catch (error: unknown) {
      setNotice(`清空记忆失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setPurging(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="text-xs text-ink-soft" htmlFor="memory-scope">
          陪伴对象（记忆作用域）
        </label>
        <select
          id="memory-scope"
          value={personaId}
          onChange={(event) => select(event.target.value)}
          className="rounded-xl border border-line bg-surface-inset px-3 py-1.5 text-xs text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {(catalog?.personas ?? []).map((persona) => (
            <option key={persona.id} value={persona.id}>
              {persona.name}
            </option>
          ))}
        </select>
        <span className="text-[11px] text-ink-faint">温层向量库与冷层事实按它隔离</span>
      </div>

      {notice ? (
        <p role="status" className="rounded-xl bg-surface-inset px-4 py-3 text-xs text-ink-muted">
          {notice}
        </p>
      ) : null}

      <div>
        <h4 className="mb-2 text-xs font-medium text-ink">会话</h4>
        <SessionList
          personaId={personaId}
          activeSessionId=""
          version={version}
          onSelect={() => setNotice("接着聊请在 Web 端或桌宠窗里打开这个会话（控制台没有对话面板）。")}
          onNew={() => setNotice("新会话在 Web 端或桌宠窗里开始；控制台只做管理。")}
          onRemove={async (sessionId) => {
            // 删除请求必须在这里发：SessionList 只做列表视图与二次确认 UI，
            // 真正调 DELETE 的一直是使用方（Web / 桌宠走 useChatSession，控制台走这一句）。
            // personaId 一定要带上：后端靠它校验会话归属，漏了会删错层的对话。
            await deleteSession(sessionId, personaId);
            setVersion((value) => value + 1);
            setNotice("已删除这段对话（不可恢复）。");
          }}
          onClearAll={async () => {
            // 控制台没有对话面板，不需要切会话指针：清完只刷新列表与提示
            const result = await deleteSessions(personaId);
            setVersion((value) => value + 1);
            setNotice(`已清空 ${result.removed_sessions} 段对话（共 ${result.removed_turns} 条消息）。`);
          }}
        />
      </div>

      <div>
        <h4 className="mb-2 text-xs font-medium text-ink">个人记忆（上传的语料）</h4>
        <KnowledgePanel companionId={personaId} companionName={personaName(catalog, personaId)} />
      </div>

      <div>
        <h4 className="mb-2 text-xs font-medium text-ink">清空记忆</h4>
        <div className="rounded-xl border border-danger/30 bg-danger-soft px-4 py-3">
          <p className="text-[11px] leading-relaxed text-danger-text">
            把「{personaName(catalog, personaId)}」的<strong>全部记忆</strong>删掉：情景记忆、
            语义事实、情绪日记、上传的语料。<strong>不可恢复</strong>。
            <br />
            会话记录<strong>不受影响</strong>（要删历史对话用上面那个「清空全部」）——
            两者刻意分开，才能「只清记忆、留着聊天记录」。
          </p>
          {pendingPurge ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="text-[11px] text-danger-text">
                确认清空？清掉就找不回来了。
              </span>
              <button
                type="button"
                onClick={() => void confirmPurge()}
                disabled={purging}
                className="focus-ring rounded-md bg-danger px-2 py-1 text-[11px] font-medium text-ink-on transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {purging ? "清空中…" : "确认清空记忆"}
              </button>
              <button
                type="button"
                onClick={() => setPendingPurge(false)}
                disabled={purging}
                className="focus-ring rounded-md border border-line px-2 py-1 text-[11px] text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed"
              >
                取消
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => {
                setPendingPurge(true);
                setNotice(null);
              }}
              disabled={purging || !personaId}
              className="focus-ring mt-2 rounded-md border border-danger/40 px-2 py-1 text-[11px] text-danger-text transition-colors hover:bg-danger/10 disabled:cursor-not-allowed disabled:opacity-60"
            >
              清空该角色的全部记忆
            </button>
          )}
        </div>
      </div>

      <div>
        <h4 className="mb-2 text-xs font-medium text-ink">记忆分层</h4>
        <dl className="rounded-xl bg-surface-inset px-4 py-3 text-[11px] leading-relaxed text-ink-muted">
          <dt className="font-medium text-ink">工作记忆</dt>
          <dd className="mb-2">
            滚动窗口，只在本会话内（由 <code className="rounded bg-surface-hover px-1">app/session/</code> 承载，不在这里管理）。
          </dd>
          <dt className="font-medium text-ink">情景记忆</dt>
          <dd className="mb-2">对话片段的混合检索召回，按陪伴对象隔离存在向量库。</dd>
          <dt className="font-medium text-ink">语义记忆</dt>
          <dd className="mb-2">结构化事实表（三元组 + 重要性 + 生命周期），常驻注入。</dd>
          <dt className="font-medium text-ink">个人记忆</dt>
          <dd>上面那块「个人记忆」面板管理的上传语料。</dd>
        </dl>
        <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">
          实时记忆追踪（每轮命中了什么、注入了多少）在对话界面的「记忆追踪」面板里看——
          它是对话过程的可视化，脱离对话没有意义。
        </p>
      </div>
    </div>
  );
}
