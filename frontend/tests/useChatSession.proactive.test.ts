"use client";

/**
 * 主动消息落到界面的测试（`docs/proactive-multimodal.md` §5.4）。
 *
 * 这条链路的要害是「**主动消息走的是同一条渲染路径**」：它由
 * `applyReply` 与用户轮共用——两处各写一遍的后果是，改了一处（比如字幕与
 * 语音同步的时机），另一处悄悄退回旧行为，而它**只在角色主动开口时才复现**。
 * 所以这里既验「消息进了列表」，也验「播报真的被驱动了」。
 *
 * 另一条要害是**排队**：用户轮进行中收到主动消息时不能并发播报
 * （SDK 的 `speak` 不能连续调用），而闸门⑤只覆盖了 2 分钟的窗口，
 * 网络往返之间仍会撞上。
 */

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatSession } from "@/hooks/chat/useChatSession";
import type { AvatarController } from "@/hooks/avatar/useAvatar";
import { getChatPreferences, getPersonas, getSessionHistory, postChat, postSpeak } from "@/lib/api/client";
import { subscribeEvents, type ProactiveMessagePayload, type SubscribeEventsOptions } from "@/lib/api/events";

vi.mock("@/lib/api/client", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly detail: unknown,
      message: string,
    ) {
      super(message);
      this.name = "ApiError";
    }
  },
  getSessionHistory: vi.fn(),
  postChat: vi.fn(),
  postSpeak: vi.fn(),
  deleteSession: vi.fn(),
  deleteSessions: vi.fn(),
  getChatPreferences: vi.fn(async () => null),
  getPersonas: vi.fn(async () => null),
  setChatPreferences: vi.fn(async (patch: Record<string, string>) => ({
    persona_id: "",
    style_id: "",
    preset_id: "",
    st_preset_id: "",
    jailbreak_id: "",
    mode: "",
    user_name: "",
    ...patch,
  })),
  getPerceptionStatus: vi.fn(async () => null),
}));

// 把 SSE 客户端换成可手工驱动的替身：jsdom 没有 EventSource，
// 走真实实现的话 hook 会静默降级为 offline，测不到主动消息路径
vi.mock("@/lib/api/events", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api/events")>("@/lib/api/events");
  return { ...actual, subscribeEvents: vi.fn() };
});

const PERSONA = "therapist-elder-sister";
const mockedSubscribe = vi.mocked(subscribeEvents);

/** 最近一次订阅的选项（测试据此手工派发事件） */
let lastOptions: SubscribeEventsOptions | null = null;
const unsubscribeSpy = vi.fn();

function fakeAvatar(): AvatarController {
  return {
    state: "idle",
    stateLabel: "待机",
    ready: true,
    provider: "server",
    stage: "ready",
    detail: "",
    setState: vi.fn(),
    speak: vi.fn(async () => {}),
    speakChunks: vi.fn(async () => {}),
    interrupt: vi.fn(),
  };
}

function proactivePayload(overrides: Partial<ProactiveMessagePayload> = {}): ProactiveMessagePayload {
  return {
    session_id: "s-proactive",
    persona_id: PERSONA,
    reply: "还没睡呀？",
    emotion: null,
    speak: null,
    trigger_id: "late-night",
    ...overrides,
  };
}

function push(payload: ProactiveMessagePayload) {
  lastOptions?.onProactiveMessage(payload);
}

beforeEach(() => {
  window.localStorage.clear();
  lastOptions = null;
  unsubscribeSpy.mockReset();
  vi.mocked(getSessionHistory).mockReset();
  vi.mocked(getSessionHistory).mockResolvedValue(null as never);
  vi.mocked(postChat).mockReset();
  vi.mocked(postSpeak).mockReset();
  vi.mocked(getPersonas).mockReset();
  vi.mocked(getPersonas).mockResolvedValue(null);
  vi.mocked(getChatPreferences).mockReset();
  vi.mocked(getChatPreferences).mockResolvedValue(null);
  mockedSubscribe.mockReset();
  mockedSubscribe.mockImplementation((options) => {
    lastOptions = options;
    options.onStateChange?.("open");
    return unsubscribeSpy;
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("主动消息通道", () => {
  it("挂载后按当前角色订阅，卸载时退订", async () => {
    const { unmount } = renderHook(() => useChatSession(fakeAvatar()));

    await waitFor(() => expect(mockedSubscribe).toHaveBeenCalled());
    expect(mockedSubscribe.mock.calls[0][0].personaId).toBe(PERSONA);

    unmount();
    expect(unsubscribeSpy).toHaveBeenCalled();
  });

  it("通道状态透出给界面（它是「它会自己开口吗」的答案）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar()));
    await waitFor(() => expect(result.current.eventChannel).toBe("open"));
  });
});

describe("收到主动消息", () => {
  it("追加一条 assistant 消息并驱动播报", async () => {
    const avatar = fakeAvatar();
    const { result } = renderHook(() => useChatSession(avatar));
    await waitFor(() => expect(lastOptions).not.toBeNull());

    await act(async () => {
      push(proactivePayload({ reply: "还没睡呀？" }));
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(result.current.messages).toHaveLength(1);
    });
    expect(result.current.messages[0]).toMatchObject({ role: "assistant", text: "还没睡呀？" });
    // 播报真的被驱动了（与用户轮共用 applyReply）
    expect(avatar.speak).toHaveBeenCalled();
    expect(avatar.setState).toHaveBeenCalledWith("speak");
  });

  it("计数与通道状态一起更新（给用户「它会主动找我」的反馈）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar()));
    await waitFor(() => expect(lastOptions).not.toBeNull());

    await act(async () => {
      push(proactivePayload());
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.proactiveCount).toBe(1));
  });

  it("会话指针跟着更新（刷新后主动消息仍在同一串对话里）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar()));
    await waitFor(() => expect(lastOptions).not.toBeNull());

    await act(async () => {
      push(proactivePayload({ session_id: "s-proactive" }));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.sessionId).toBe("s-proactive"));
  });

  it("**不**写入用户消息（合成输入是内部指令，不是用户说的话）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar()));
    await waitFor(() => expect(lastOptions).not.toBeNull());

    await act(async () => {
      push(proactivePayload({ reply: "在想什么呢？" }));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.messages).toHaveLength(1));
    expect(result.current.messages.some((message) => message.role === "user")).toBe(false);
    expect(result.current.messages[0].text).not.toContain("系统触发");
  });

  it("情绪透出（表情跟着变）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar()));
    await waitFor(() => expect(lastOptions).not.toBeNull());

    await act(async () => {
      push(
        proactivePayload({
          emotion: {
            label: "calm",
            label_zh: "平静",
            intensity: 0.3,
            confidence: 0.9,
            facial_expression: "default",
            source: "llm",
          },
        }),
      );
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.emotion?.label).toBe("calm"));
  });
});

describe("用户轮进行中收到主动消息", () => {
  it("排队等这一轮说完，**不打断**也不并发播报", async () => {
    let resolveChat: ((value: unknown) => void) | null = null;
    vi.mocked(postChat).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveChat = resolve;
        }) as never,
    );

    const avatar = fakeAvatar();
    const { result } = renderHook(() => useChatSession(avatar));
    await waitFor(() => expect(lastOptions).not.toBeNull());

    // 用户发一条，卡在等待后端
    await act(async () => {
      void result.current.send("在吗");
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.busy).toBe(true));

    // 此刻收到主动消息 → 应当排队而不是立刻播报
    await act(async () => {
      push(proactivePayload({ reply: "我先说一句" }));
      await Promise.resolve();
    });
    expect(result.current.messages.some((m) => m.text === "我先说一句")).toBe(false);
    expect(result.current.proactiveCount).toBe(0);

    // 放行用户轮
    await act(async () => {
      resolveChat?.({
        session_id: "s-user",
        persona_id: PERSONA,
        reply: "嗯，我在。",
        emotion: null,
        speak: null,
        memory_counts: {},
        memory_scheduled: true,
        knowledge_hits: 0,
        worldbook_hits: [],
        warnings: [],
        estimated_tokens: 0,
        style: {},
        preset: {},
        tools_used: [],
        st_preset: {},
        note: "",
      });
      await Promise.resolve();
    });

    // 用户轮结束后，排队的那条才出现
    await waitFor(() =>
      expect(result.current.messages.some((m) => m.text === "我先说一句")).toBe(true),
    );
    expect(result.current.proactiveCount).toBe(1);
  });
});
