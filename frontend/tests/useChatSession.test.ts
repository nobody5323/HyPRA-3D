"use client";

/**
 * `useChatSession` 的会话恢复行为测试。
 *
 * 这里锁住两处**只能靠时序复现**的问题（手测几乎撞不到，但真实网络下会发生）：
 * 1. 恢复历史期间发消息 —— 迟到的历史响应不得覆盖刚产生的新消息；
 * 2. 历史恢复失败 —— 必须暴露错误（而不是静默空白），且能重试。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatSession } from "@/hooks/chat/useChatSession";
import type { AvatarController } from "@/hooks/avatar/useAvatar";
import { deleteSession, getSessionHistory, postChat, postSpeak } from "@/lib/api/client";
import { writeActiveSessionId } from "@/lib/chat/session-store";
import { getChatPreferences, setChatPreferences } from "@/lib/api/client";
import type { ChatResponse, SessionHistory, SpeakCommand } from "@/lib/api/types";

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
  // 对话偏好：默认「后端读不到」（null）——与本测试关心的历史/播报无关，
  // 这样 hook 会保持初始值，不会干扰下面的断言
  getChatPreferences: vi.fn(async () => null),
  setChatPreferences: vi.fn(async (patch: Record<string, string>) => ({
    persona_id: "",
    style_id: "",
    preset_id: "",
    st_preset_id: "",
    ...patch,
  })),
}));

const PERSONA = "therapist-elder-sister";

/** 假的具身控制器：本测试只关心对话状态，不关心播报 */
const fakeAvatar: AvatarController = {
  state: "idle",
  stateLabel: "待机",
  ready: true,
  provider: "browser",
  stage: "ready",
  detail: "",
  setState: vi.fn(),
  speak: vi.fn(async () => {}),
  speakChunks: vi.fn(async () => {}),
  interrupt: vi.fn(),
};

/** 构造一条最小可用的 /chat 响应 */
function chatResponse(overrides: Partial<ChatResponse> = {}): ChatResponse {
  return {
    session_id: "s-new",
    persona_id: PERSONA,
    reply: "嗯，我在。",
    emotion: null,
    system_prompt: "",
    messages: [],
    worldbook_hits: [],
    skipped: [],
    memory_counts: {},
    memory_scheduled: true,
    knowledge_hits: 0,
    warnings: [],
    estimated_tokens: 0,
    style: {},
    preset: {},
    speak: null,
    tools_used: [],
    note: "",
    ...overrides,
  } as ChatResponse;
}

function sessionHistory(messages: { role: string; text: string }[]): SessionHistory {
  return {
    session_id: "s-old",
    persona_id: PERSONA,
    user_name: "小林",
    messages: messages.map((message) => ({ ...message, created_at: null })),
  };
}

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(getSessionHistory).mockReset();
  vi.mocked(postChat).mockReset();
  vi.mocked(postSpeak).mockReset();
  vi.mocked(deleteSession).mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useChatSession 历史恢复", () => {
  it("刷新后有本地指针时自动拉取历史并填充消息", async () => {
    writeActiveSessionId(PERSONA, "s-old");
    vi.mocked(getSessionHistory).mockResolvedValue(
      sessionHistory([
        { role: "user", text: "我之前说过失眠" },
        { role: "assistant", text: "嗯，记得" },
      ]),
    );

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.restoring).toBe(false));
    expect(result.current.sessionId).toBe("s-old");
    expect(result.current.messages.map((message) => message.text)).toEqual([
      "我之前说过失眠",
      "嗯，记得",
    ]);
  });

  it("没有本地指针时保持新对话（不请求历史）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.restoring).toBe(false));
    expect(getSessionHistory).not.toHaveBeenCalled();
    expect(result.current.messages).toEqual([]);
  });

  it("恢复期间发消息：迟到的历史响应不得覆盖新消息", async () => {
    writeActiveSessionId(PERSONA, "s-old");

    // 让历史请求悬挂，手动控制返回时机（模拟慢网络）
    let resolveHistory!: (value: SessionHistory) => void;
    vi.mocked(getSessionHistory).mockReturnValue(
      new Promise<SessionHistory>((resolve) => {
        resolveHistory = resolve;
      }),
    );
    vi.mocked(postChat).mockResolvedValue(chatResponse());

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.restoring).toBe(true));

    // 历史还没回来就发消息
    await act(async () => {
      await result.current.send("你好");
    });
    expect(result.current.messages.map((message) => message.text)).toEqual([
      "你好",
      "嗯，我在。",
    ]);

    // 迟到的历史响应到达 → 必须被丢弃（否则会把刚产生的两条抹掉）
    await act(async () => {
      resolveHistory(sessionHistory([{ role: "user", text: "很久以前的旧消息" }]));
    });
    expect(result.current.messages.map((message) => message.text)).toEqual([
      "你好",
      "嗯，我在。",
    ]);
  });

  it("恢复失败时暴露 historyError，重试成功后恢复消息", async () => {
    writeActiveSessionId(PERSONA, "s-old");
    vi.mocked(getSessionHistory).mockRejectedValueOnce(new Error("网络错误"));

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.historyError).toBe("网络错误"));
    expect(result.current.restoring).toBe(false);
    expect(result.current.messages).toEqual([]);

    vi.mocked(getSessionHistory).mockResolvedValueOnce(
      sessionHistory([{ role: "user", text: "旧消息" }]),
    );
    await act(async () => {
      await result.current.retryHistory();
    });

    expect(result.current.historyError).toBeNull();
    expect(result.current.messages.map((message) => message.text)).toEqual(["旧消息"]);
  });

  it("新建对话后清空界面与本地指针（历史仍留在后端）", async () => {
    writeActiveSessionId(PERSONA, "s-old");
    vi.mocked(getSessionHistory).mockResolvedValue(
      sessionHistory([{ role: "user", text: "旧消息" }]),
    );

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.messages).toHaveLength(1));

    act(() => {
      result.current.newSession();
    });

    expect(result.current.messages).toEqual([]);
    expect(result.current.sessionId).toBeNull();
    expect(window.localStorage.getItem(`hypra.active-session.${PERSONA}`)).toBeNull();
  });

  it("删除当前会话：界面回到新对话，并清掉本地指针", async () => {
    writeActiveSessionId(PERSONA, "s-cur");
    vi.mocked(getSessionHistory).mockResolvedValue({
      ...sessionHistory([{ role: "user", text: "历史消息" }]),
      session_id: "s-cur",
    });
    vi.mocked(deleteSession).mockResolvedValue({ session_id: "s-cur", removed_turns: 2 });

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.sessionId).toBe("s-cur"));

    await act(async () => {
      await result.current.removeSession("s-cur");
    });

    expect(deleteSession).toHaveBeenCalledWith("s-cur", PERSONA);
    expect(result.current.messages).toEqual([]);
    expect(result.current.sessionId).toBeNull();
    // 指针必须一起清：否则刷新后又会去拉一个已删除的会话（白跑一轮 404）
    expect(window.localStorage.getItem(`hypra.active-session.${PERSONA}`)).toBeNull();
  });

  it("删除其它会话：不动当前界面，只发出列表刷新信号", async () => {
    writeActiveSessionId(PERSONA, "s-cur");
    vi.mocked(getSessionHistory).mockResolvedValue({
      ...sessionHistory([{ role: "user", text: "历史消息" }]),
      session_id: "s-cur",
    });
    vi.mocked(deleteSession).mockResolvedValue({ session_id: "s-other", removed_turns: 2 });

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.sessionId).toBe("s-cur"));
    const before = result.current.sessionVersion;

    await act(async () => {
      await result.current.removeSession("s-other");
    });

    expect(result.current.messages).toHaveLength(1); // 当前会话不受影响
    expect(result.current.sessionId).toBe("s-cur");
    expect(result.current.sessionVersion).toBe(before + 1);
  });
});

/**
 * 播报上下文：服务端 TTS（GPT-SoVITS）用本轮情绪去换后端的表情时间轴，
 * 而情绪是本轮对话的产物、hook 调用时还不知道，所以必须**跟着 speak 传进去**。
 * 这里锁「传了」与「没情绪时传 null/默认强度」（漏传会静默变成中性表情）。
 */
describe("useChatSession 对话偏好", () => {
  it("后端已有偏好时初值采用它（用户偏好优先于部署默认）", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce({
      persona_id: PERSONA,
      style_id: "stored-style",
      preset_id: "stored-preset",
      st_preset_id: "",
    });

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.styleId).toBe("stored-style"));
    expect(result.current.presetId).toBe("stored-preset");
    // 页面靠它决定要不要再用部署默认校准
    expect(result.current.hasStoredPreference).toBe(true);
  });

  it("后端读不到偏好时保持初始值，且不标记为已存", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce(null);

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.hasStoredPreference).toBe(false));
    expect(result.current.styleId).toBe("modern-conversational");
  });

  it("切换文风会把选择写回后端（三端共享的那一份）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.hasStoredPreference).toBe(false));

    act(() => result.current.setStyleId("another-style"));

    await waitFor(() =>
      expect(setChatPreferences).toHaveBeenCalledWith({ style_id: "another-style" }),
    );
    expect(result.current.styleId).toBe("another-style");
  });

  it("切换人设同样写回（人设 id 也是记忆命名空间）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.hasStoredPreference).toBe(false));

    act(() => result.current.setPersonaId("another-persona"));

    await waitFor(() =>
      expect(setChatPreferences).toHaveBeenCalledWith({ persona_id: "another-persona" }),
    );
  });
});

describe("useChatSession 播报上下文", () => {
  it("整段播报带上本轮情绪与强度", async () => {
    vi.mocked(postChat).mockResolvedValue(
      chatResponse({
        emotion: {
      label: "anxious",
      label_zh: "焦虑",
      intensity: 0.8,
      confidence: 0.9,
      facial_expression: "frowning_worry",
      source: "llm",
    },
        speak: {
          ssml: "<speak>嗯</speak>",
          display_text: "嗯，我在。",
          voice: "V1",
          emotion: "anxious",
          ka_action: "",
          tone: "柔声",
          intensity: 0.8,
        },
      }),
    );
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await act(async () => {
      await result.current.send("在吗");
    });

    expect(vi.mocked(fakeAvatar.speak)).toHaveBeenCalledWith("嗯，我在。", "<speak>嗯</speak>", {
      emotion: "anxious",
      intensity: 0.8,
    });
  });

  it("分段播报同样带上（音频整段合成靠它对齐表情）", async () => {
    vi.mocked(postChat).mockResolvedValue(
      chatResponse({ emotion: {
      label: "anxious",
      label_zh: "焦虑",
      intensity: 0.8,
      confidence: 0.9,
      facial_expression: "frowning_worry",
      source: "llm",
    }, reply: "第一段。第二段。" }),
    );
    vi.mocked(postSpeak).mockResolvedValue({
      ssml: "<speak>第一段。第二段。</speak>",
      display_text: "第一段。第二段。",
      voice: "V1",
      emotion: "anxious",
      ka_action: "",
      tone: "柔声",
      intensity: 0.8,
      chunks: ["第一段。", "第二段。"],
      ssml_chunks: ["<speak>第一段。</speak>", "<speak>第二段。</speak>"],
    } as SpeakCommand);

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    act(() => {
      result.current.setStreamingSpeech(true);
    });

    await act(async () => {
      await result.current.send("在吗");
    });

    expect(vi.mocked(fakeAvatar.speakChunks)).toHaveBeenCalledWith(
      [
        { text: "第一段。", ssml: "<speak>第一段。</speak>" },
        { text: "第二段。", ssml: "<speak>第二段。</speak>" },
      ],
      expect.any(Function),
      { emotion: "anxious", intensity: 0.8 },
    );
  });

  it("没有情绪时传 null 与默认强度（后端据此用中性表情）", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse({ emotion: null }));
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await act(async () => {
      await result.current.send("在吗");
    });

    expect(vi.mocked(fakeAvatar.speak)).toHaveBeenCalledWith("嗯，我在。", undefined, {
      emotion: null,
      intensity: 0.5,
    });
  });
});
