"use client";

/**
 * 对话面板：消息列表 + 输入框 + Agent 工具提示 + 打断按钮。
 *
 * 性能：输入框抽为 ChatComposer（draft 状态内聚）——按键不再重渲染消息列表；
 *      消息列表用 memo 包裹，仅在消息数量/忙碌态变化时重渲染。
 * 可访问性：消息区 role="log" + aria-live（读屏播报新回复）、错误条 role="alert"、
 *          输入框带可访问名（sr-only label）。
 */

import { memo, useEffect, useId, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";

import type { ChatMessage, ToolUsage } from "@/lib/types";

/** 工具调用 → 用户可读提示（体现「能办事」） */
const TOOL_LABELS: Record<string, string> = {
  record_mood_journal: "已记录这次心情",
  query_mood_trend: "已查看情绪趋势",
  start_breathing_exercise: "已准备好呼吸引导",
  recall_memory: "已检索长期记忆",
};

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

/**
 * 消息列表（memo 包裹：输入框打字不会触发它的重渲染）。
 * role="log" + aria-live 让读屏用户能听到苏澄的回复与工具结果。
 */
const MessageList = memo(function MessageList({
  messages,
  toolNotes,
  busy,
  listRef,
}: {
  messages: ChatMessage[];
  toolNotes: string[];
  busy: boolean;
  listRef: RefObject<HTMLDivElement | null>;
}) {
  return (
    <div
      ref={listRef}
      role="log"
      aria-live="polite"
      aria-label="对话消息"
      className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain px-4 py-4"
    >
      {messages.length === 0 && (
        <p className="mt-6 text-center text-sm text-ink-soft">
          说点什么吧——比如「我最近总是失眠」。
        </p>
      )}

      {messages.map((message, index) => (
        <div
          // 优先用消息自带 id；兼容旧数据时退回「角色 + 下标」，不使用裸下标
          key={message.id ?? `${message.role}-${index}`}
          className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div
            className={`max-w-[85%] whitespace-pre-wrap break-words rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
              message.role === "user" ? "bg-accent text-ink-on" : "bg-surface-raised text-ink"
            }`}
          >
            {message.text}
          </div>
        </div>
      ))}

      {toolNotes.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {toolNotes.map((note) => (
            <span
              key={note}
              className="inline-flex items-center gap-1 rounded-full bg-success-soft px-2.5 py-1 text-xs text-success-text ring-1 ring-success/30"
            >
              ✓ {note}
            </span>
          ))}
        </div>
      )}

      {busy && (
        <div className="flex justify-start">
          <div className="rounded-2xl bg-surface-raised px-4 py-2.5 text-sm text-ink-soft">
            苏澄正在回应…
          </div>
        </div>
      )}
    </div>
  );
});

/** 输入区：draft 状态内聚于此，打字只重渲染本组件（不再牵动整棵消息列表） */
function ChatComposer({
  busy,
  onSend,
  onInterrupt,
}: {
  busy: boolean;
  onSend: (text: string) => void;
  onInterrupt: () => void;
}) {
  const [draft, setDraft] = useState("");
  const inputId = useId();

  function submit() {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  }

  return (
    <footer className="border-t border-line px-4 py-3">
      <div className="flex items-end gap-2">
        {/* 可访问名：读屏不会把 placeholder 当作标签 */}
        <label htmlFor={inputId} className="sr-only">
          给苏澄的消息
        </label>
        <textarea
          id={inputId}
          name="message"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          rows={2}
          placeholder="说说你今天怎么样…（Enter 发送，Shift+Enter 换行）"
          className="min-h-[44px] flex-1 resize-none rounded-xl border border-line bg-surface-inset px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        />
        {busy ? (
          <button
            type="button"
            onClick={onInterrupt}
            className={`h-11 shrink-0 rounded-xl bg-danger px-4 text-sm font-medium text-ink-on transition-colors hover:bg-danger/90 ${FOCUS_RING}`}
          >
            打断
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!draft.trim()}
            className={`h-11 shrink-0 rounded-xl bg-accent px-4 text-sm font-medium text-ink-on transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-ink-faint ${FOCUS_RING}`}
          >
            发送
          </button>
        )}
      </div>
    </footer>
  );
}

export function ChatPanel({
  messages,
  toolsUsed,
  busy,
  error,
  onSend,
  onInterrupt,
}: {
  messages: ChatMessage[];
  toolsUsed: ToolUsage[];
  busy: boolean;
  error: string | null;
  onSend: (text: string) => void;
  onInterrupt: () => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  // 依赖「消息数量 + 忙碌态」而非 messages 引用：只有内容真正增长时才滚动
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, busy]);

  // memo 依赖：toolsUsed 不变时不重建数组，避免 MessageList 白白重渲染
  const toolNotes = useMemo(
    () =>
      toolsUsed
        .map((tool) => TOOL_LABELS[tool.name])
        .filter((label): label is string => Boolean(label)),
    [toolsUsed],
  );

  return (
    <section className="flex h-full min-h-0 flex-col rounded-2xl border border-line bg-surface-panel">
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-sm font-medium text-ink">对话</h2>
        <span className="text-xs text-ink-soft">
          {messages.length > 0 ? `${messages.length} 条消息` : "开始聊聊吧"}
        </span>
      </header>

      <MessageList messages={messages} toolNotes={toolNotes} busy={busy} listRef={listRef} />

      {error && (
        <p
          role="alert"
          className="mx-4 mb-2 break-words rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger-text"
        >
          {error}
        </p>
      )}

      <ChatComposer busy={busy} onSend={onSend} onInterrupt={onInterrupt} />
    </section>
  );
}
