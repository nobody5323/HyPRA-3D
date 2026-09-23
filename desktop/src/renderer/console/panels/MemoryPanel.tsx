import { useEffect, useState } from "react";

import { SessionList } from "@/components/chat/SessionList";
import { KnowledgePanel } from "@/components/studio/KnowledgePanel";
import { getPersonas } from "@/lib/api/client";
import type { PersonaCatalog } from "@/lib/api/types";

/**
 * 会话与记忆分组。
 *
 * 三块：
 * - **会话列表**：浏览与删除（新建与「接着聊」在 Web / 桌宠界面里做——控制台没有对话面板，
 *   在这里造一个空会话只会得到一个点了没反应的入口）；
 * - **个人记忆**：上传语料、查看分块（`KnowledgePanel` 本身自包含）；
 * - **记忆架构说明**：实时记忆追踪（`MemoryTrace`）是**对话过程**的可视化，
 *   脱离对话语境没有意义，所以这里不放它，改为写清四层记忆的分工。
 */

/** 会话所属的陪伴对象（记忆作用域 `companion:{id}`） */
function usePersonaScope(): { personaId: string; catalog: PersonaCatalog | null; select: (id: string) => void } {
  const [catalog, setCatalog] = useState<PersonaCatalog | null>(null);
  const [personaId, setPersonaId] = useState("");

  useEffect(() => {
    let active = true;

    void getPersonas().then((next) => {
      if (!active) return;
      setCatalog(next);
      setPersonaId(next?.default_persona_id ?? "");
    });

    return () => {
      active = false;
    };
  }, []);

  return { personaId, catalog, select: setPersonaId };
}

export function MemoryPanel() {
  const { personaId, catalog, select } = usePersonaScope();
  /** 列表刷新版本（删除会话后自增） */
  const [version, setVersion] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

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
          onRemove={async () => {
            // 删完之后列表要重新拉（SessionList 内部自己调删除接口）
            setVersion((value) => value + 1);
          }}
        />
      </div>

      <div>
        <h4 className="mb-2 text-xs font-medium text-ink">个人记忆（上传的语料）</h4>
        <KnowledgePanel companionId={personaId} />
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
