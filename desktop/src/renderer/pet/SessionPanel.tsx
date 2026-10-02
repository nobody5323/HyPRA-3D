import { useCallback, useEffect, useRef, useState } from "react";

import { getSessions } from "@/lib/api/client";
import type { PersonaCatalog, SessionSummary } from "@/lib/api/types";
import { personaName } from "@/lib/chat/persona";

/**
 * 角色与会话（桌宠窗自己的入口）。
 *
 * 为什么要有它：桌宠窗是长期开着的那个窗口，但换角色、翻历史会话原本只能去
 * 控制台或 Web 端——用户在这里找不到入口，就会以为「没法切换会话」。
 *
 * **样式不能借 Web 端的组件**：桌宠入口不加载 Tailwind（`pet/main.tsx` 只引
 * `pet.css`），引 `@/components/*` 会渲染成没有样式的裸元素；那条边界由
 * `desktop/tests/pet-renderer-boundary.test.ts` 守着。所以这里一律用
 * `.petField` / `.petHint` / `.petSessionList` 这套桌宠自己的类，尺寸跟着
 * `--pet-scale` 走。
 */

/** 桌宠窗要用到的会话能力（从 App 传一个最小集合，不透传整个 session 对象） */
export interface PetChatSettings {
  /** 当前角色（人设）id；同时是记忆隔离命名空间 */
  personaId: string;
  /** 当前会话 id（null = 新对话） */
  sessionId: string | null;
  /** 会话数据版本：变化即重拉列表（发消息 / 新建 / 删除后自增） */
  version: number;
  /** 人设清单；null = 尚未取回（后端离线时也会是 null） */
  catalog: PersonaCatalog | null;
  selectPersona(id: string): void;
  openSession(id: string): Promise<void>;
  newSession(): void;
  removeSession(id: string): Promise<void>;
  clearAll(): Promise<void>;
  /** 对话进行中：禁止切换与删除（避免与正在跑的回合并发改状态） */
  busy: boolean;
}

/**
 * ISO 时间 → 相对时间（「3 分钟前」比绝对时间好认）。
 *
 * 与 Web 端列表同口径，但**刻意各留一份**：那份在 Tailwind 组件
 * （`frontend/components/chat/SessionList.tsx`）里，桌宠窗不能引它。
 */
function formatRelative(iso: string): string {
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return "";
  const diff = Date.now() - time;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "刚刚";
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`;
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`;
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}

export function SessionPanel({
  personaId,
  sessionId,
  version,
  catalog,
  selectPersona,
  openSession,
  newSession,
  removeSession,
  clearAll,
  busy,
}: PetChatSettings) {
  /** null = 尚未加载（与「已加载但没有会话」是两件事，文案不同） */
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState("");
  /** 正在二次确认删除的会话 id / 清空全部 */
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [pendingClear, setPendingClear] = useState(false);
  /** 列表请求或删除/清空进行中 */
  const [working, setWorking] = useState(false);
  /** 请求序号：切换角色后丢弃在途的旧响应（否则新角色下会闪一下旧角色的会话） */
  const seqRef = useRef(0);

  const reload = useCallback(async () => {
    const seq = ++seqRef.current;
    try {
      // 一次取够：后端默认只回 20 条，少报了会让用户以为「删不完」
      const items = await getSessions(personaId, { limit: 100 });
      if (seq !== seqRef.current) return;
      setSessions(items);
      setError("");
    } catch (err: unknown) {
      if (seq !== seqRef.current) return;
      setSessions([]);
      setError(err instanceof Error ? err.message : "读取历史会话失败");
    }
  }, [personaId]);

  // 换角色：先清空本地列表与确认态，避免旧角色的条目停在新角色下面
  useEffect(() => {
    seqRef.current += 1;
    setSessions(null);
    setError("");
    setPendingDelete(null);
    setPendingClear(false);
  }, [personaId]);

  // 挂载、换角色、会话数据变化（发消息 / 新建 / 删除 / 清空）都重拉
  useEffect(() => {
    void reload();
  }, [reload, version]);

  /** 跑一个会失败的操作：统一置忙、把后端给的原因显示出来 */
  async function run(action: () => Promise<void>) {
    setWorking(true);
    setError("");
    try {
      await action();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setWorking(false);
    }
  }

  const personas = catalog?.personas ?? [];
  const blocked = busy || working;
  const knownCount = sessions?.length ?? 0;

  return (
    <>
      {personas.length === 0 ? (
        <p className="petHint">角色清单未加载（后端未连接？）：暂时只能在当前角色下对话。</p>
      ) : (
        <label className="petField">
          <span>
            角色 <strong>{personaName(catalog, personaId)}</strong>
          </span>
          <select
            value={personaId}
            disabled={blocked}
            onChange={(event) => selectPersona(event.target.value)}
          >
            {personas.map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.name}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="petFieldRow">
        <button type="button" onClick={newSession} disabled={blocked}>
          新对话
        </button>
        {pendingClear ? (
          <>
            <button
              type="button"
              className="petSessionDanger"
              disabled={blocked}
              onClick={() => {
                setPendingClear(false);
                void run(clearAll);
              }}
            >
              确认清空
            </button>
            <button type="button" onClick={() => setPendingClear(false)} disabled={working}>
              取消
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={blocked || knownCount === 0}
            onClick={() => {
              setPendingClear(true);
              setPendingDelete(null);
            }}
          >
            清空全部
          </button>
        )}
      </div>

      {pendingClear ? (
        <p className="petHint" role="status">
          清空该角色的 {knownCount} 段对话？不可恢复；记忆不受影响。
        </p>
      ) : null}

      {sessions === null ? <p className="petHint">会话加载中…</p> : null}

      {sessions !== null && knownCount === 0 && !working ? (
        <p className="petHint">还没有历史对话。聊过一轮之后，这里就会出现，随时可以点回来。</p>
      ) : null}

      {sessions !== null && knownCount > 0 ? (
        <ul className="petSessionList">
          {sessions.map((item) => {
            const current = item.session_id === sessionId;
            return (
              <li
                key={item.session_id}
                className="petSessionItem"
                data-current={current ? "true" : "false"}
              >
                <button
                  type="button"
                  className="petSessionOpen"
                  aria-current={current ? "true" : undefined}
                  disabled={blocked}
                  onClick={() => void run(() => openSession(item.session_id))}
                >
                  <span className="petSessionTitle">{item.title}</span>
                  <span className="petSessionMeta">
                    <span>{item.message_count} 条</span>
                    <span>{formatRelative(item.updated_at)}</span>
                    {current ? <span>当前</span> : null}
                  </span>
                </button>

                {pendingDelete === item.session_id ? (
                  <>
                    <button
                      type="button"
                      className="petSessionDelete petSessionDanger"
                      disabled={blocked}
                      onClick={() => {
                        setPendingDelete(null);
                        void run(() => removeSession(item.session_id));
                      }}
                    >
                      确认删
                    </button>
                    <button
                      type="button"
                      className="petSessionDelete"
                      disabled={working}
                      onClick={() => setPendingDelete(null)}
                    >
                      取消
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="petSessionDelete"
                    disabled={blocked}
                    onClick={() => {
                      setPendingDelete(item.session_id);
                      setPendingClear(false);
                    }}
                  >
                    删除
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}

      {error ? (
        <p className="petWarning" role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}
