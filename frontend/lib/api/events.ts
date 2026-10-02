/**
 * 事件推送通道（SSE）——主动消息的**唯一入口**。
 *
 * 契约见 `docs/proactive-multimodal.md` §5.1 与后端 `app/api/events.py`。
 * 刻意**独立成模块**而不是塞进 `lib/api/client.ts`：client 是请求-响应式封装的集合，
 * 而这是一条长连接，生命周期与错误语义都不一样（自动重连、断线状态机）。
 *
 * ## 为什么用 EventSource 而不是自己写 fetch 流
 *
 * 浏览器原生 `EventSource` 自带**自动重连**（默认 3 秒，服务端可用 `retry:` 调整），
 * 自己实现要处理重连退避、`Last-Event-ID`、半包解析——而主动消息是**单向**的，
 * 用不上 WebSocket 的双工能力。这一条选择省掉的是整块基础设施。
 *
 * ## 拿不到 EventSource 时必须是**静默降级**
 *
 * 不是所有环境都有它（SSR 预渲染、jsdom 单测、部分内嵌 WebView）。
 * 这时返回一个空退订函数，而不是抛错——主动沟通是**增强**，
 * 它不可用不该让对话界面起不来。
 */

import { API_BASE } from "@/lib/api/client";
import type { EmotionInfo, SpeakCommand } from "@/lib/api/types";

// ---------- 事件类型（与 backend/app/events/types.py 一一对应）----------

/** 主动开口：payload 与 `POST /chat` 的响应**同构**，因此可以复用同一套渲染。 */
export const EVENT_PROACTIVE_MESSAGE = "proactive_message";
/** 被节制层拦下（可观测性：调参靠它，而不是靠猜「为什么它不说话」）。 */
export const EVENT_PROACTIVE_SKIPPED = "proactive_skipped";
/** 感知状态提示（如「此刻在听什么」徽标）。 */
export const EVENT_PERCEPTION_HINT = "perception_hint";

/** 连接状态（界面据此显示「会自己开口」是否在线）。 */
export type EventChannelState = "connecting" | "open" | "offline";

/** 主动开口的负载（与 `ChatResponse` 同构的那部分）。 */
export interface ProactiveMessagePayload {
  session_id: string;
  persona_id: string;
  reply: string;
  emotion?: EmotionInfo | null;
  speak?: SpeakCommand | null;
  /** 触发它的触发器 id（调试用） */
  trigger_id?: string;
  /** 触发原因（内部信息，展示与否由界面决定） */
  reason?: string;
  /** 服务端时间戳（ISO） */
  at?: string;
}

export interface PerceptionHintPayload {
  source: string;
  kind: string;
  summary?: string;
}

export interface ProactiveSkippedPayload {
  trigger: string;
  reason: string;
}

export interface SubscribeEventsOptions {
  /** 订阅哪个陪伴对象的事件（作用域隔离：切换角色不该收到另一个角色的消息） */
  personaId: string;
  onProactiveMessage: (payload: ProactiveMessagePayload) => void;
  onPerceptionHint?: (payload: PerceptionHintPayload) => void;
  onProactiveSkipped?: (payload: ProactiveSkippedPayload) => void;
  onStateChange?: (state: EventChannelState) => void;
}

/** 事件是否可用（SSR / jsdom / 部分内嵌 WebView 里没有 EventSource）。 */
export function eventsSupported(): boolean {
  return typeof globalThis.EventSource !== "undefined";
}

/**
 * 订阅主动消息。返回退订函数（组件卸载时必须调用）。
 *
 * `personaId` 为空时不建立连接——后端要求声明在看哪个角色，
 * 不声明就只能靠猜，而猜错的表现是「收到另一个角色的话」。
 */
export function subscribeEvents(options: SubscribeEventsOptions): () => void {
  const personaId = (options.personaId || "").trim();
  if (!personaId || !eventsSupported()) {
    options.onStateChange?.("offline");
    return () => {};
  }

  const url = `${API_BASE}/events?persona_id=${encodeURIComponent(personaId)}`;
  let source: EventSource;
  try {
    source = new EventSource(url);
  } catch {
    // 构造失败（URL 非法 / 被 CSP 拦）——当作离线，不影响对话
    options.onStateChange?.("offline");
    return () => {};
  }

  options.onStateChange?.("connecting");

  source.onopen = () => options.onStateChange?.("open");
  // EventSource 会自动重连，因此这里**不关闭连接**：断线时只更新状态，
  // 让浏览器自己退避重试。主动消息晚到一分钟，远好过彻底不再连。
  source.onerror = () => options.onStateChange?.("connecting");

  const parse = <T,>(event: MessageEvent): T | null => {
    try {
      return JSON.parse(event.data as string) as T;
    } catch {
      return null;
    }
  };

  // 就绪帧：后端建连后立刻发一条，前端据此确认「通道通了」，
  // 而不是靠「等很久没消息」来推断——那种推断在「通道坏了」和
  // 「确实没有主动消息」之间无法区分。
  source.addEventListener(EVENT_PERCEPTION_HINT, (event) => {
    const payload = parse<PerceptionHintPayload & { kind?: string }>(event as MessageEvent);
    if (payload?.kind === "ready") {
      options.onStateChange?.("open");
      return;
    }
    if (payload) options.onPerceptionHint?.(payload);
  });

  source.addEventListener(EVENT_PROACTIVE_MESSAGE, (event) => {
    const payload = parse<ProactiveMessagePayload>(event as MessageEvent);
    if (payload) options.onProactiveMessage(payload);
  });

  source.addEventListener(EVENT_PROACTIVE_SKIPPED, (event) => {
    const payload = parse<ProactiveSkippedPayload>(event as MessageEvent);
    if (payload) options.onProactiveSkipped?.(payload);
  });

  return () => {
    source.close();
    options.onStateChange?.("offline");
  };
}
