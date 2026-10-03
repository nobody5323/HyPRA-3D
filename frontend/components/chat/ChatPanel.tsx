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

import { MicButton } from "@/components/chat/MicButton";
import { ImageButton } from "@/components/chat/ImageButton";
import { CapabilityHint } from "@/components/chat/CapabilityHint";
import type { EventChannelState } from "@/lib/api/events";
import type { ChatMessage, ToolUsage } from "@/lib/api/types";
import { useImageShare } from "@/hooks/perception/useImageShare";
import { usePerceptionCapabilities } from "@/hooks/perception/usePerceptionCapabilities";
import { useSpeechInput } from "@/hooks/perception/useSpeechInput";
import { PERSONA_NAME_FALLBACK } from "@/lib/chat/persona";
import { readDraft, writeDraft } from "@/lib/chat/session-store";

/**
 * 发图后填进输入框的那句话。
 *
 * 刻意**不填图片描述**：描述已经作为感知事实由 `LAYER_PERCEPTION` 注入
 * （见 `useImageShare` 的说明），再写进用户消息等于说两遍。
 */
const IMAGE_SHARE_DRAFT = "给你看一张图";

/** 内置工具 → 用户可读提示（体现「能办事」） */
const TOOL_LABELS: Record<string, string> = {
  record_mood_journal: "已记录这次心情",
  query_mood_trend: "已查看情绪趋势",
  start_breathing_exercise: "已准备好呼吸引导",
  recall_memory: "已检索长期记忆",
};

/**
 * 工具名 → 展示文案。
 *
 * MCP 工具命名约定为 `mcp__<服务器>__<工具>`（见 backend/app/mcp/bridge.py），
 * 这里按来源统一展示为「已调用外部服务」，具体工具名在 hover 提示里可见。
 */
function toolLabel(name: string): string | null {
  const builtin = TOOL_LABELS[name];
  if (builtin) return builtin;
  if (name.startsWith("mcp__")) {
    const server = name.split("__")[1];
    return server ? `已调用外部服务「${server}」` : "已调用外部服务";
  }
  return null;
}

/** 键盘焦点样式（浅色主题：鼠尾草绿环） */
const FOCUS_RING =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-surface-panel";

/**
 * 空态快捷话题。
 *
 * 空面板只写一句「说点什么吧」时，第一次用的人会卡在「说什么」上。
 * 给几个具体的开场（对应本项目真实会处理的场景：睡眠、压力、情绪），
 * 点一下就能把对话推起来——它们只是**把文本填进输入框并发送**，没有任何特殊逻辑。
 */
const STARTERS = ["我最近总是失眠", "今天工作压力好大", "陪我随便聊聊天"];

/**
 * 消息列表（memo 包裹：输入框打字不会触发它的重渲染）。
 * role="log" + aria-live 让读屏用户能听到角色的回复与工具结果。
 */
const MessageList = memo(function MessageList({
  messages,
  toolNotes,
  busy,
  listRef,
  characterName,
  onSend,
}: {
  messages: ChatMessage[];
  toolNotes: string[];
  busy: boolean;
  listRef: RefObject<HTMLDivElement | null>;
  /** 当前角色名（只说名字，不带定位；用于「正在回应…」） */
  characterName: string;
  /** 空态快捷话题的发送入口（点击即发） */
  onSend: (text: string) => void;
}) {
  return (
    <div
      ref={listRef}
      role="log"
      aria-live="polite"
      aria-label="对话消息"
      className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain px-4 py-5"
    >
      {messages.length === 0 && (
        <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
          <span
            aria-hidden="true"
            className="grid h-12 w-12 place-items-center rounded-2xl bg-accent-soft text-accent-text"
          >
            <svg
              viewBox="0 0 24 24"
              className="h-6 w-6"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.8}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 12a8 8 0 0 1-8 8H8l-4 3v-5.5A8 8 0 1 1 21 12Z" />
              <path d="M9 11h6M9 14.5h3.5" />
            </svg>
          </span>
          <div>
            <p className="text-sm font-medium text-ink">开始和{characterName}聊聊</p>
            <p className="mt-1 text-xs leading-relaxed text-ink-soft">
              说说今天怎么样，或者直接从下面挑一句。
            </p>
          </div>
          <div className="flex flex-wrap justify-center gap-2">
            {STARTERS.map((starter) => (
              <button
                key={starter}
                type="button"
                onClick={() => onSend(starter)}
                className={`chip chip-neutral transition-colors hover:bg-surface-hover hover:text-ink ${FOCUS_RING}`}
              >
                {starter}
              </button>
            ))}
          </div>
        </div>
      )}

      {messages.map((message, index) => {
        const mine = message.role === "user";

        return (
          <div
            // 优先用消息自带 id；兼容旧数据时退回「角色 + 下标」，不使用裸下标
            key={message.id ?? `${message.role}-${index}`}
            className={`flex animate-fade-up ${mine ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[min(85%,42rem)] whitespace-pre-wrap break-words px-4 py-2.5 text-sm leading-relaxed ${
                mine
                  ? "rounded-2xl rounded-br-md bg-accent text-ink-on shadow-highlight"
                  : "rounded-2xl rounded-bl-md bg-surface-raised text-ink ring-1 ring-line/70"
              }`}
            >
              {message.text}
            </div>
          </div>
        );
      })}

      {toolNotes.length > 0 && (
        <div className="flex flex-wrap gap-2 pt-1">
          {toolNotes.map((note) => (
            <span key={note} className="chip chip-success">
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-3 w-3"
                fill="none"
                stroke="currentColor"
                strokeWidth={2.5}
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="m5 13 4 4L19 7" />
              </svg>
              {note}
            </span>
          ))}
        </div>
      )}

      {busy && (
        <div className="flex justify-start">
          <div className="flex items-center gap-2 rounded-2xl rounded-bl-md bg-surface-raised px-4 py-2.5 text-sm text-ink-soft ring-1 ring-line/70">
            {/* 三点跳动比一行文字更符合「正在输入」的直觉，且不抢读屏（文字仍在） */}
            <span aria-hidden="true" className="flex items-center gap-1">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-ink-faint"
                  style={{ animationDelay: `${i * 200}ms` }}
                />
              ))}
            </span>
            <span translate="no">{characterName}</span>正在回应…
          </div>
        </div>
      )}
    </div>
  );
});

/** 输入区：draft 状态内聚于此，打字只重渲染本组件（不再牵动整棵消息列表） */
function ChatComposer({
  busy,
  restoring,
  personaId,
  characterName,
  onSend,
  onInterrupt,
}: {
  busy: boolean;
  /** 正在恢复历史：允许打字但不发送（否则会落到错误的会话） */
  restoring?: boolean;
  /** 陪伴对象 id：草稿按角色分开保存（切换角色不串草稿） */
  personaId: string;
  /** 当前角色名（输入框的可访问名用） */
  characterName: string;
  onSend: (text: string) => void;
  onInterrupt: () => void;
}) {
  const [draft, setDraft] = useState("");
  const inputId = useId();
  /**
   * 后端具备哪些感知能力（语音 / 图片）——决定画不画对应入口。
   *
   * 探测放在**输入区自己**（而不是页面）里：这两个按钮只在这里用，
   * 而 ChatPanel 的 props 面已经不小了，为它们再加四个透传参数
   * 只会让三个界面都要跟着改。探测失败 = 不渲染按钮
   * （见 `usePerceptionCapabilities` 的约定）。
   */
  const perception = usePerceptionCapabilities();

  /** 把感知结果填进输入框（**不自动发送**：用户看一眼再决定） */
  function fillDraft(text: string) {
    setDraft(text);
    writeDraft(personaId, text);
  }

  // 语音：转写结果回填（见 useSpeechInput 的模块文档）
  const speech = useSpeechInput({
    enabled: perception.asr,
    onText: fillDraft,
    // 快捷键由用户在插件设置里自己填（「哪个键顺手」因人而异，
    // 而且不同人的软件环境里被占用的键也不同）
    shortcut: perception.asrShortcut,
  });

  /**
   * 图片：理解结果**不回填描述**，只回填一句「给你看一张图」。
   *
   * 描述本身已经作为**感知事实**进了后端快照（`/perception/vision`），
   * 会由 `LAYER_PERCEPTION` 注入；再把它塞进用户消息等于说两遍，
   * 而且会变成「用户转述了自己发的图」这种奇怪的角色。
   */
  const image = useImageShare({
    enabled: perception.vision,
    onReady: () => fillDraft(IMAGE_SHARE_DRAFT),
  });

  /** 语音与图片的提示合并显示：两者共用同一块位置，不各占一行 */
  const notice = speech.error ?? image.error;
  const clearNotice = () => {
    speech.clearError();
    image.clearError();
  };

  // 草稿本地持久化：刷新页面后未发送的内容还在。
  // 写入放在输入事件里（而不是 useEffect）：否则挂载时「先读草稿」与
  // 「用空串保存」两个 effect 的执行顺序会把已保存的草稿清掉。
  useEffect(() => {
    setDraft(readDraft(personaId));
  }, [personaId]);

  function submit() {
    const text = draft.trim();
    // 恢复历史期间不发送：此时 sessionId 尚未就绪，发出会新建/写错会话
    if (!text || busy || restoring) return;
    onSend(text);
    setDraft("");
    writeDraft(personaId, "");
  }

  return (
    <footer className="border-t border-line bg-surface-panel px-4 py-3">
      {/* 上手引导：只显示一次，关掉后记住（见 CapabilityHint 的模块文档） */}
      <CapabilityHint
        showMic={perception.showMic}
        showImage={perception.showImage}
        shortcutLabel={speech.shortcutLabel}
        hintId="chat-input-capabilities"
      />
      {notice && (
        <p role="alert" className="notice notice-warning mb-2 flex flex-wrap items-center gap-2 break-words text-xs">
          <span className="break-words">{notice}</span>
          <button
            type="button"
            onClick={clearNotice}
            className={`btn btn-sm btn-quiet ${FOCUS_RING}`}
          >
            知道了
          </button>
        </p>
      )}
      <div className="flex items-end gap-2">
        {/* 可访问名：读屏不会把 placeholder 当作标签 */}
        <label htmlFor={inputId} className="sr-only">
          给{characterName}的消息
        </label>
        <textarea
          id={inputId}
          name="message"
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            writeDraft(personaId, event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          rows={2}
          placeholder="说说你今天怎么样…（Enter 发送，Shift+Enter 换行）"
          className="field min-h-[44px] flex-1 resize-none leading-relaxed"
        />
        <MicButton speech={speech} show={perception.showMic} />
        <ImageButton image={image} show={perception.showImage} />
        {busy ? (
          <button
            type="button"
            onClick={onInterrupt}
            className={`btn btn-danger h-11 ${FOCUS_RING}`}
            title="停止这一轮回复"
          >
            打断
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!draft.trim() || restoring}
            className={`btn btn-primary h-11 ${FOCUS_RING}`}
          >
            <svg
              aria-hidden="true"
              viewBox="0 0 24 24"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M4 12h15M13 6l6 6-6 6" />
            </svg>
            发送
          </button>
        )}
      </div>
    </footer>
  );
}

/**
 * 主动沟通状态徽标（`docs/proactive-multimodal.md` §5.1）。
 *
 * 它回答的是用户心里的一个问题：「它会自己找我说话吗？」
 * ——而主动沟通的**物理前提**正是这条推送通道是否连着，所以直接把通道状态显示出来
 * 是最诚实的做法（而不是写一句「支持主动沟通」却看不出有没有生效）。
 *
 * 三个状态各自对应一个可行动的判断：
 * - `open`     通道已连接，它随时可能开口
 * - `connecting` 正在连 / 断线重连中（浏览器会自动重试）
 * - `offline`  不支持或没连上（后端未启动 / 还没确定角色）
 */
function ProactiveBadge({
  channel,
  count,
  hint,
}: {
  channel: EventChannelState;
  count: number;
  hint: string;
}) {
  if (channel === "offline") return null;
  const open = channel === "open";
  const title = open
    ? "推送通道已连接：到点或感知到合适的时机，它会自己开口（可在「能力中心 → 主动沟通」里调免打扰与频率）"
    : "正在连接推送通道…（断线会自动重试）";
  return (
    <span
      className="chip chip-neutral shrink-0"
      title={title}
      translate="no"
    >
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${open ? "bg-success" : "bg-ink-faint"}`}
      />
      <span className="sr-only">{title}</span>
      {open ? "会主动找你" : "连接中"}
      {count > 0 && <span className="tabular-nums text-ink-faint">·{count}</span>}
      {hint && (
        <span className="max-w-[12rem] truncate text-ink-faint" title={hint}>
          {hint}
        </span>
      )}
    </span>
  );
}

export function ChatPanel({
  messages,
  toolsUsed,
  busy,
  error,
  onSend,
  onInterrupt,
  streamingSpeech,
  onToggleStreaming,
  personaId,
  characterName = PERSONA_NAME_FALLBACK,
  restoring,
  historyError,
  onRetryHistory,
  eventChannel = "offline",
  proactiveCount = 0,
  perceptionHint = "",
}: {
  messages: ChatMessage[];
  toolsUsed: ToolUsage[];
  busy: boolean;
  error: string | null;
  onSend: (text: string) => void;
  onInterrupt: () => void;
  /** 分段播报开关（默认开；关闭时整段合成播出——首句更慢但一次成形） */
  streamingSpeech: boolean;
  onToggleStreaming: (on: boolean) => void;
  /** 当前陪伴对象 id（草稿按角色分开保存） */
  personaId: string;
  /**
   * 当前角色名（「正在回应…」与输入框可访问名用）。
   * 缺省用中性兜底词，**不回退具体角色名**——内置人设只是示例。
   */
  characterName?: string;
  /** 正在从后端恢复历史（刷新页面 / 打开历史会话） */
  restoring?: boolean;
  /** 历史恢复失败的提示（非空时显示 + 提供重试） */
  historyError?: string | null;
  onRetryHistory?: () => void;
  /** 主动消息通道（SSE）状态；`offline` 时不渲染徽标 */
  eventChannel?: EventChannelState;
  /** 本次页面会话内主动开口的次数 */
  proactiveCount?: number;
  /** 感知提示摘要（如「用户正在听《起风了》」） */
  perceptionHint?: string;
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
        .map((tool) => toolLabel(tool.name))
        .filter((label): label is string => Boolean(label)),
    [toolsUsed],
  );

  return (
    <section
      aria-labelledby="chat-title"
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-line bg-surface-panel shadow-card"
    >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 id="chat-title" className="truncate text-sm font-semibold text-ink">
            与 <span translate="no">{characterName}</span> 对话
          </h2>
          <span className="shrink-0 text-xs tabular-nums text-ink-faint">
            {messages.length > 0 ? `${messages.length} 条` : "尚未开始"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ProactiveBadge
            channel={eventChannel}
            count={proactiveCount}
            hint={perceptionHint}
          />
          <label className="flex shrink-0 items-center gap-1.5 text-xs text-ink-soft">
            <input
              type="checkbox"
              checked={streamingSpeech}
              onChange={(event) => onToggleStreaming(event.target.checked)}
              disabled={busy}
              title="逐段播报：首段更早出声、字幕与语音同步出现（服务端 TTS 段间无缝；魔珐 SDK 段间约 400ms 过渡）"
              className="focus-ring accent-accent disabled:cursor-not-allowed"
            />
            分段播报
          </label>
        </div>
      </header>

      {restoring && messages.length === 0 && (
        <p role="status" className="px-4 py-3 text-xs text-ink-soft">
          正在恢复历史记录…
        </p>
      )}
      <MessageList
        messages={messages}
        toolNotes={toolNotes}
        busy={busy}
        listRef={listRef}
        characterName={characterName}
        onSend={onSend}
      />

      {historyError && (
        <div role="alert" className="notice notice-warning mx-4 mb-2 flex flex-wrap items-center gap-2">
          <span className="break-words">历史记录读取失败：{historyError}</span>
          <button
            type="button"
            onClick={onRetryHistory}
            className={`btn btn-sm btn-danger-outline ${FOCUS_RING}`}
          >
            重试
          </button>
        </div>
      )}

      {error && (
        <p role="alert" className="notice notice-danger mx-4 mb-2 break-words">
          {error}
        </p>
      )}

      <ChatComposer
        busy={busy}
        restoring={restoring}
        personaId={personaId}
        characterName={characterName}
        onSend={onSend}
        onInterrupt={onInterrupt}
      />
    </section>
  );
}
