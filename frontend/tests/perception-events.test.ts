/**
 * SSE 事件通道测试。
 *
 * 这里锁住三件只能靠「没连上时会发生什么」暴露出来的事：
 * 1. 没有 EventSource 的环境（jsdom / SSR / 部分内嵌 WebView）必须**静默降级**，
 *    而不是抛错——主动沟通是增强，它不可用不该让对话界面起不来；
 * 2. 没有角色 id 时不建连接（否则后端只能猜，猜错的表现是「收到别人的消息」）；
 * 3. 退订必须真的 close（漏了会留下永远不退订的连接）。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  EVENT_PERCEPTION_HINT,
  EVENT_PROACTIVE_MESSAGE,
  EVENT_PROACTIVE_SKIPPED,
  eventsSupported,
  subscribeEvents,
} from "@/lib/api/events";

/** 最小的 EventSource 替身：记录 URL、允许手工派发事件。 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, Set<(event: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  close() {
    this.closed = true;
  }

  /** 测试用：模拟服务端推来一帧 */
  emit(type: string, payload: unknown) {
    const event = { data: JSON.stringify(payload) } as MessageEvent;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }

  /** 测试用：模拟服务端推来一帧**坏 JSON** */
  emitRaw(type: string, raw: string) {
    const event = { data: raw } as MessageEvent;
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

const originalEventSource = globalThis.EventSource;

beforeEach(() => {
  FakeEventSource.instances = [];
  (globalThis as { EventSource?: unknown }).EventSource = FakeEventSource;
});

afterEach(() => {
  if (originalEventSource) {
    (globalThis as { EventSource?: unknown }).EventSource = originalEventSource;
  } else {
    delete (globalThis as { EventSource?: unknown }).EventSource;
  }
});

function lastSource(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (!source) throw new Error("没有建立 EventSource 连接");
  return source;
}

describe("能力探测", () => {
  it("有 EventSource 时可用", () => {
    expect(eventsSupported()).toBe(true);
  });

  it("没有 EventSource 时不可用", () => {
    delete (globalThis as { EventSource?: unknown }).EventSource;
    expect(eventsSupported()).toBe(false);
  });
});

describe("subscribeEvents", () => {
  it("没有 EventSource 时静默降级为离线（不抛错）", () => {
    delete (globalThis as { EventSource?: unknown }).EventSource;
    const onStateChange = vi.fn();

    const unsubscribe = subscribeEvents({
      personaId: "p1",
      onProactiveMessage: vi.fn(),
      onStateChange,
    });

    expect(FakeEventSource.instances).toHaveLength(0);
    expect(onStateChange).toHaveBeenCalledWith("offline");
    expect(() => unsubscribe()).not.toThrow();
  });

  it("没有角色 id 时不建连接", () => {
    const onStateChange = vi.fn();

    subscribeEvents({ personaId: "   ", onProactiveMessage: vi.fn(), onStateChange });

    expect(FakeEventSource.instances).toHaveLength(0);
    expect(onStateChange).toHaveBeenCalledWith("offline");
  });

  it("连接地址带角色 id（作用域隔离）", () => {
    subscribeEvents({ personaId: "苏澄 老姐", onProactiveMessage: vi.fn() });

    expect(lastSource().url).toContain("/events?persona_id=");
    expect(lastSource().url).toContain(encodeURIComponent("苏澄 老姐"));
  });

  it("建连时状态为 connecting，收到就绪帧后转为 open", () => {
    const states: string[] = [];
    subscribeEvents({
      personaId: "p1",
      onProactiveMessage: vi.fn(),
      onStateChange: (state) => states.push(state),
    });

    expect(states).toEqual(["connecting"]);

    lastSource().emit(EVENT_PERCEPTION_HINT, { source: "events", kind: "ready" });
    expect(states).toEqual(["connecting", "open"]);
  });

  it("就绪帧不当作感知提示回调（它是通道握手，不是内容）", () => {
    const onPerceptionHint = vi.fn();
    subscribeEvents({ personaId: "p1", onProactiveMessage: vi.fn(), onPerceptionHint });

    lastSource().emit(EVENT_PERCEPTION_HINT, { source: "events", kind: "ready" });
    expect(onPerceptionHint).not.toHaveBeenCalled();

    lastSource().emit(EVENT_PERCEPTION_HINT, {
      source: "desktop",
      kind: "summary",
      summary: "用户正在听《起风了》",
    });
    expect(onPerceptionHint).toHaveBeenCalledWith(
      expect.objectContaining({ summary: "用户正在听《起风了》" }),
    );
  });

  it("主动消息按类型分派", () => {
    const onProactiveMessage = vi.fn();
    subscribeEvents({ personaId: "p1", onProactiveMessage });

    lastSource().emit(EVENT_PROACTIVE_MESSAGE, {
      session_id: "s1",
      persona_id: "p1",
      reply: "还没睡呀？",
      trigger_id: "late-night",
    });

    expect(onProactiveMessage).toHaveBeenCalledWith(
      expect.objectContaining({ reply: "还没睡呀？", trigger_id: "late-night" }),
    );
  });

  it("被拦下的原因按类型分派（可观测性）", () => {
    const onProactiveSkipped = vi.fn();
    subscribeEvents({ personaId: "p1", onProactiveMessage: vi.fn(), onProactiveSkipped });

    lastSource().emit(EVENT_PROACTIVE_SKIPPED, {
      trigger: "daily-greeting",
      reason: "当前是免打扰时段（23:00-08:00）",
    });

    expect(onProactiveSkipped).toHaveBeenCalledWith({
      trigger: "daily-greeting",
      reason: "当前是免打扰时段（23:00-08:00）",
    });
  });

  it("坏 JSON 不抛错、不回调", () => {
    const onProactiveMessage = vi.fn();
    subscribeEvents({ personaId: "p1", onProactiveMessage });

    expect(() => lastSource().emitRaw(EVENT_PROACTIVE_MESSAGE, "{ 坏掉的")).not.toThrow();
    expect(onProactiveMessage).not.toHaveBeenCalled();
  });

  it("断线只更新状态、**不关闭连接**（交给浏览器自动重连）", () => {
    const onStateChange = vi.fn();
    subscribeEvents({ personaId: "p1", onProactiveMessage: vi.fn(), onStateChange });

    const source = lastSource();
    source.onerror?.();

    expect(source.closed).toBe(false);
    expect(onStateChange).toHaveBeenLastCalledWith("connecting");
  });

  it("退订会关闭连接并回到 offline", () => {
    const onStateChange = vi.fn();
    const unsubscribe = subscribeEvents({
      personaId: "p1",
      onProactiveMessage: vi.fn(),
      onStateChange,
    });

    const source = lastSource();
    unsubscribe();

    expect(source.closed).toBe(true);
    expect(onStateChange).toHaveBeenLastCalledWith("offline");
  });

  it("构造失败（URL 非法 / 被 CSP 拦）也降级为离线", () => {
    class ThrowingEventSource {
      constructor() {
        throw new Error("blocked by CSP");
      }
    }
    (globalThis as { EventSource?: unknown }).EventSource = ThrowingEventSource;

    const onStateChange = vi.fn();
    expect(() =>
      subscribeEvents({ personaId: "p1", onProactiveMessage: vi.fn(), onStateChange }),
    ).not.toThrow();
    expect(onStateChange).toHaveBeenCalledWith("offline");
  });
});
