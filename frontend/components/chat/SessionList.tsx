"use client";

/**
 * 历史记录面板：会话列表 + 新建对话。
 *
 * 会话由后端持久化（`chat_sessions` / `chat_turns`），本组件只是它的视图：
 * 点选某条 → 交给 `useChatSession.openSession` 去后端拉那次会话的消息。
 * 「新建对话」不会删除任何东西，只是把当前会话指针清空并开一段新的——
 * 旧会话仍留在列表里，随时可以点回来（演示「记忆仍在、对话重开」时很有用）。
 *
 * 无障碍：与知识库面板一致（折叠触发器带 `aria-expanded` / `aria-controls`，
 * 当前会话用 `aria-current` 标记，结果为 `role="status"`）。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getSessions } from "@/lib/api/client";
import type { SessionSummary } from "@/lib/api/types";

/** ISO 时间 → 相对时间（历史列表里「3 分钟前」比绝对时间更好认） */
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

export function SessionList({
  personaId,
  activeSessionId,
  /** 会话数据版本（发送成功 / 新建后自增）→ 触发列表刷新 */
  version,
  disabled = false,
  onSelect,
  onNew,
  onRemove,
}: {
  personaId: string;
  activeSessionId: string | null;
  version: number;
  /** 对话进行中时禁止切换/删除会话（避免与正在跑的回合并发改状态） */
  disabled?: boolean;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  /** 删除一段对话（不可恢复；父层会在删到当前会话时回到新对话） */
  onRemove: (sessionId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  /** null = 尚未加载 */
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 正在二次确认删除的会话 id */
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  /** 正在删除的会话 id（防重复点击） */
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  /** 请求序号：切换陪伴对象后丢弃在途的旧响应 */
  const seqRef = useRef(0);

  const reload = useCallback(async () => {
    const seq = ++seqRef.current;
    setLoading(true);
    try {
      const items = await getSessions(personaId);
      if (seq !== seqRef.current) return;
      setSessions(items);
      setError(null);
    } catch (err) {
      if (seq !== seqRef.current) return;
      setSessions([]);
      setError(err instanceof Error ? err.message : "读取历史记录失败");
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }, [personaId]);

  // 切换陪伴对象：清空上一个角色的列表并作废在途请求
  useEffect(() => {
    seqRef.current += 1;
    setSessions(null);
    setError(null);
    setPendingDelete(null);
    setDeleteError(null);
  }, [personaId]);

  // 展开时加载；会话数据变化（发送成功 / 新建）时若已展开则刷新
  useEffect(() => {
    if (!open) return;
    void reload();
  }, [open, reload, version]);

  const panelId = "session-list-panel";
  const titleId = "session-list-title";

  /** 删除（已在界面上过了二次确认） */
  async function confirmDelete(sessionId: string) {
    setPendingDelete(null);
    setDeleting(sessionId);
    setDeleteError(null);
    try {
      await onRemove(sessionId);
      // 不在这里 reload：父层删完会自增 version，上面的 effect 会自动重新拉列表
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <section aria-labelledby={titleId} className="rounded-xl border border-line bg-surface-panel">
      <h2 id={titleId} className="sr-only">
        历史记录
      </h2>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((prev) => !prev)}
        className="focus-ring flex w-full items-center justify-between rounded-xl px-4 py-3 text-left transition-colors hover:bg-surface-hover"
      >
        <span className="flex items-baseline gap-2">
          <span className="text-xs text-ink-muted">历史记录</span>
          <span className="text-xs text-ink-faint">
            {sessions === null ? "刷新后也能接着聊" : `${sessions.length} 段对话`}
          </span>
        </span>
        <span aria-hidden="true" className="text-xs text-ink-soft">
          {open ? "收起" : "展开"}
        </span>
      </button>

      {open && (
        <div
          id={panelId}
          aria-busy={loading}
          className="max-h-[40vh] space-y-3 overflow-y-auto border-t border-line px-4 py-3"
        >
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onNew}
              disabled={disabled}
              className="focus-ring flex-1 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint"
            >
              新建对话
            </button>
            <button
              type="button"
              onClick={() => void reload()}
              disabled={disabled || loading}
              className="focus-ring rounded-lg border border-line px-3 py-2 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:text-ink-faint"
            >
              {loading ? "刷新中…" : "刷新"}
            </button>
          </div>
          <p className="text-xs leading-relaxed text-ink-faint">
            新建对话不会删除任何记录：旧对话仍在这里，随时可以点回来。
          </p>

          {error && (
            <p role="alert" className="break-words text-xs leading-relaxed text-danger-text">
              {error}
            </p>
          )}

          {sessions === null && <p className="text-xs text-ink-faint">加载中…</p>}

          {sessions !== null && sessions.length === 0 && !loading && (
            <p className="text-xs leading-relaxed text-ink-faint">
              还没有历史记录。聊过一轮之后，这里就会出现当时的对话。
            </p>
          )}

          {deleteError && (
            <p role="alert" className="break-words text-xs leading-relaxed text-danger-text">
              {deleteError}
            </p>
          )}

          {sessions !== null && sessions.length > 0 && (
            <ul className="space-y-2">
              {sessions.map((session) => {
                const active = session.session_id === activeSessionId;
                const busyThis = deleting === session.session_id;
                return (
                  <li
                    key={session.session_id}
                    className={`rounded-lg border transition-colors ${
                      active
                        ? "border-accent/40 bg-accent-soft"
                        : "border-line bg-surface-inset"
                    }`}
                  >
                    <div className="flex items-stretch gap-1 px-3 py-2">
                      {/* 列表项正文：单独一个按钮（不能把删除按钮套在里面，按钮不能嵌套） */}
                      <button
                        type="button"
                        aria-current={active ? "true" : undefined}
                        disabled={disabled || deleting !== null}
                        onClick={() => onSelect(session.session_id)}
                        className="focus-ring flex-1 rounded-md text-left disabled:cursor-not-allowed"
                      >
                        <span className="flex items-baseline justify-between gap-2">
                          <span
                            className={`break-words text-sm ${
                              active ? "text-accent-text" : "text-ink"
                            }`}
                          >
                            {session.title}
                          </span>
                          {active && (
                            <span className="shrink-0 text-xs text-accent-text">当前</span>
                          )}
                        </span>
                        <span className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-ink-soft">
                          <span className="tabular-nums">{session.message_count} 条</span>
                          <span className="tabular-nums">
                            {formatRelative(session.updated_at)}
                          </span>
                        </span>
                      </button>

                      {/* 删除：内联二次确认（不用 window.confirm，阻塞式且键盘体验差） */}
                      <div className="flex shrink-0 items-center gap-1">
                        {pendingDelete === session.session_id ? (
                          <>
                            <button
                              type="button"
                              onClick={() => void confirmDelete(session.session_id)}
                              disabled={disabled || deleting !== null}
                              className="focus-ring rounded-md bg-danger px-2 py-1 text-xs font-medium text-ink-on transition-colors hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {busyThis ? "删除中…" : "确认删除"}
                            </button>
                            <button
                              type="button"
                              onClick={() => setPendingDelete(null)}
                              disabled={busyThis}
                              className="focus-ring rounded-md border border-line px-2 py-1 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed"
                            >
                              取消
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            onClick={() => {
                              setPendingDelete(session.session_id);
                              setDeleteError(null);
                            }}
                            disabled={disabled || deleting !== null}
                            className="focus-ring rounded-md border border-line px-2 py-1 text-xs text-ink-muted transition-colors hover:bg-surface-hover disabled:cursor-not-allowed disabled:text-ink-faint"
                          >
                            删除
                          </button>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
