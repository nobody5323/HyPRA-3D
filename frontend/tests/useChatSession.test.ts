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

import { useChatSession } from "@/hooks/useChatSession";
import type { AvatarController } from "@/hooks/useAvatar";
import { deleteSession, getSessionHistory, postChat } from "@/lib/api";
import { writeActiveSessionId } from "@/lib/session-store";
import type { ChatResponse, SessionHistory } from "@/lib/types";

vi.mock("@/lib/api", () => ({
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
