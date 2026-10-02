"use client";

/**
 * `useChatSession` 的会话恢复行为测试。
 *
 * 这里锁住两处**只能靠时序复现**的问题（手测几乎撞不到，但真实网络下会发生）：
 * 1. 恢复历史期间发消息 —— 迟到的历史响应不得覆盖刚产生的新消息；
 * 2. 历史恢复失败 —— 必须暴露错误（而不是静默空白），且能重试。
 */

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatSession } from "@/hooks/chat/useChatSession";
import type { AvatarController } from "@/hooks/avatar/useAvatar";
import { deleteSession, deleteSessions, getSessionHistory, postChat, postSpeak } from "@/lib/api/client";
import { MODE_TAVERN } from "@/lib/chat/mode";
import { writeActiveSessionId } from "@/lib/chat/session-store";
import { getChatPreferences, getPersonas, setChatPreferences } from "@/lib/api/client";
import type { ChatPreferences } from "@/lib/api/client";
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
  deleteSessions: vi.fn(),
  // 对话偏好：默认「后端读不到」（null）——与本测试关心的历史/播报无关，
  // 这样 hook 会保持初始值，不会干扰下面的断言
  getChatPreferences: vi.fn(async () => null),
  // 人设清单：默认也取不到（null）→ hook 的「角色失效自愈」直接跳过，
  // 不影响下面那些与角色无关的用例；要测自愈的用例自己 mockResolvedValue
  getPersonas: vi.fn(async () => null),
  setChatPreferences: vi.fn(async (patch: Record<string, string>) => ({
    persona_id: "",
    style_id: "",
    preset_id: "",
    st_preset_id: "",
    // 叙事框架：`""` = 未设置（跟随部署默认）；`"none"` 是另一回事（显式关闭）
    jailbreak_id: "",
    mode: "",
    user_name: "",
    ...patch,
  })),
}));

const PERSONA = "therapist-elder-sister";

/** 假的具身控制器：本测试只关心对话状态，不关心播报 */
const fakeAvatar: AvatarController = {
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
  // `clearAllMocks` 只清调用记录、不清实现，所以这里必须显式把清单恢复成
  // 「取不到」——否则前一个用例设的清单会漏给下一个，自愈逻辑被意外触发
  vi.mocked(getPersonas).mockReset();
  vi.mocked(getPersonas).mockResolvedValue(null);
});

afterEach(() => {
  /*
   * 必须显式卸载：本仓库的 vitest 关掉了 globals（`vitest.config.mts`），
   * Testing Library 的自动清理因此不会注册——不卸载的话，前一个用例 render 出来的
   * hook 会一直挂在 window / document 上，它注册的监听器（如跨界面同步的 focus）
   * 会在下一个用例里抢着响应事件，表现就是「同样的断言，单独跑过、一起跑就挂」。
   */
  cleanup();
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
      jailbreak_id: "",
      mode: "",
      user_name: "阿岸",
    });

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.styleId).toBe("stored-style"));
    expect(result.current.presetId).toBe("stored-preset");
    // 用户称呼也在同一份共享偏好里（不是写死的默认人名）
    expect(result.current.userName).toBe("阿岸");
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

  it("切角色立刻清空界面，不等新历史回来（否则失败时残留上一个角色的对话）", async () => {
    writeActiveSessionId(PERSONA, "s-old");
    vi.mocked(getSessionHistory).mockResolvedValue(
      sessionHistory([{ role: "user", text: "上一个角色那边的对话" }]),
    );

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.messages.map((m) => m.text)).toEqual([
      "上一个角色那边的对话",
    ]));

    // 新角色的历史请求一直不返回（真实网络里就是超时/失败那条路）
    vi.mocked(getSessionHistory).mockImplementation(() => new Promise(() => {}));

    act(() => result.current.setPersonaId("energetic-roommate"));

    // 界面必须已经是空的、指针也已断开，不能还留着上个角色的内容
    expect(result.current.messages).toEqual([]);
    expect(result.current.sessionId).toBeNull();
  });

  it("清空全部：调后端批量删除，并回到干净的新对话", async () => {
    writeActiveSessionId(PERSONA, "s-old");
    vi.mocked(getSessionHistory).mockResolvedValue(
      sessionHistory([{ role: "user", text: "旧内容" }]),
    );
    vi.mocked(deleteSessions).mockResolvedValue({
      persona_id: PERSONA,
      removed_sessions: 3,
      removed_turns: 6,
    });

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.sessionId).toBe("s-old"));

    await act(async () => {
      await result.current.clearAllSessions();
    });

    expect(deleteSessions).toHaveBeenCalledWith(PERSONA);
    expect(result.current.sessionId).toBeNull();
    expect(result.current.messages).toEqual([]);
  });

  it("没设置过称呼时用默认「朋友」，不是某个具体人名", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce({
      persona_id: "",
      style_id: "",
      preset_id: "",
      st_preset_id: "",
      jailbreak_id: "",
      mode: "",
      user_name: "",
    });

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.hasStoredPreference).toBe(false));
    expect(result.current.userName).toBe("朋友");
  });

  it("改称呼写回后端并用返回值回填，且不切会话、不动记忆", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.hasStoredPreference).toBe(false));

    vi.mocked(setChatPreferences).mockResolvedValueOnce({
      persona_id: "",
      style_id: "",
      preset_id: "",
      st_preset_id: "",
      jailbreak_id: "",
      mode: "",
      user_name: "阿岸",
    });

    await act(async () => {
      await result.current.setUserName("  阿岸  ");
    });

    // 去空白后写入；本地用后端返回的那个值（不是本地草稿）
    expect(setChatPreferences).toHaveBeenCalledWith({ user_name: "阿岸" });
    expect(result.current.userName).toBe("阿岸");
    // 称呼只影响提示词，不涉及会话归属与记忆：不该因此去拉历史
    expect(getSessionHistory).not.toHaveBeenCalled();
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

  it("静默路径（未接入 TTS）不请求分段，字幕直接显示整段回复", async () => {
    // 分段播报的意义是「音频逐段出声、字幕跟着推进」；没有音频时它只是白白多一次
    // /media/speak 请求，还会让字幕在几段之间快速跳变
    vi.mocked(postChat).mockResolvedValue(chatResponse({ reply: "第一段。第二段。" }));
    const silentAvatar: AvatarController = { ...fakeAvatar, provider: "none" };
    const { result } = renderHook(() => useChatSession(silentAvatar));

    await act(async () => {
      await result.current.send("在吗");
    });

    expect(vi.mocked(postSpeak)).not.toHaveBeenCalled();
    expect(vi.mocked(silentAvatar.speakChunks)).not.toHaveBeenCalled();
    expect(vi.mocked(silentAvatar.speak)).toHaveBeenCalledWith(
      "第一段。第二段。",
      undefined,
      { emotion: null, intensity: 0.5 },
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

/**
 * 交互模式（`AGENTS.md §8.1`）：请求要带上它，切换要开新对话。
 *
 * 这两条都**不能只靠界面测试守**：漏传 `mode` 时后端会按酒馆预设推断，界面上
 * 看不出任何异常，但用户手选的模式就白选了；而切模式不清会话，会让同一段对话
 * 前后半段的记忆口径不一致（前半按桌宠隔离、后半突然召回酒馆世界书）。
 */
describe("useChatSession 交互模式", () => {
  it("请求体带上用户选定的模式", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse({ mode: MODE_TAVERN }));
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    act(() => result.current.setMode(MODE_TAVERN));
    await act(async () => {
      await result.current.send("你好");
    });

    expect(vi.mocked(postChat).mock.calls[0][0]).toMatchObject({ mode: MODE_TAVERN });
  });

  it("没选过模式时传 null（交给后端按酒馆预设推断）", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse());
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await act(async () => {
      await result.current.send("你好");
    });

    expect(vi.mocked(postChat).mock.calls[0][0]).toMatchObject({ mode: null });
  });

  it("★ 切换模式会开新对话（清掉消息与会话指针）", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse());
    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await act(async () => {
      await result.current.send("你好");
    });
    expect(result.current.sessionId).toBe("s-new");

    act(() => result.current.setMode(MODE_TAVERN));

    expect(result.current.messages).toEqual([]);
    expect(result.current.sessionId).toBeNull();
  });

  it("★ 已经是当前模式时不重复清空（点一下不该把对话弄没）", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse({ mode: MODE_TAVERN }));
    const { result } = renderHook(() => useChatSession(fakeAvatar));
    act(() => result.current.setMode(MODE_TAVERN));
    await act(async () => {
      await result.current.send("你好");
    });

    // 后端实际跑的就是酒馆模式 → 再点一次只是「确认」，不该清掉对话
    act(() => result.current.setMode(MODE_TAVERN));

    expect(result.current.messages).toHaveLength(2);
  });

  it("★ 没选过、但后端已推断成酒馆模式时，点酒馆模式同样不清空", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse({ mode: MODE_TAVERN }));
    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await act(async () => {
      await result.current.send("你好");
    });

    act(() => result.current.setMode(MODE_TAVERN));

    expect(result.current.messages).toHaveLength(2);
  });

  it("记住用户的选择（三个界面共享同一份偏好）", async () => {
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    act(() => result.current.setMode(MODE_TAVERN));

    expect(vi.mocked(setChatPreferences)).toHaveBeenCalledWith({ mode: MODE_TAVERN });
  });

  it("后端实际生效的模式单独暴露（面板显示用，不参与请求）", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse({ mode: MODE_TAVERN }));
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await act(async () => {
      await result.current.send("你好");
    });

    expect(result.current.effectiveMode).toBe(MODE_TAVERN);
    expect(result.current.mode).toBe("");
  });
});

describe("useChatSession 角色失效自愈（404 未知人设）", () => {
  /** 只含一个角色的清单（模拟「用户原来选的那个已经被删了」） */
  function catalogWith(id: string, name: string) {
    return {
      default_persona_id: id,
      personas: [{ id, name, title: "", description: "", tags: [] }],
    };
  }

  it("★ 当前角色已不存在 → 自动切到可用角色，并把原因说清楚", async () => {
    vi.mocked(getPersonas).mockResolvedValue(catalogWith("sharp-tongued-partner", "陆星野"));

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => {
      expect(result.current.personaId).toBe("sharp-tongued-partner");
    });
    // 换角色 = 换一整套记忆（人设 id 是记忆命名空间），必须让用户看得见
    expect(result.current.error).toContain("已不存在");
    expect(result.current.error).toContain("陆星野");
  });

  it("当前角色仍在清单里 → 原样保留，不产生任何提示", async () => {
    vi.mocked(getPersonas).mockResolvedValue(catalogWith(PERSONA, "苏澄"));

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(vi.mocked(getPersonas)).toHaveBeenCalled());
    expect(result.current.personaId).toBe(PERSONA);
    expect(result.current.error).toBeNull();
  });

  it("清单为空时不乱换角色（宁可让错误可见，也不切到不存在的东西）", async () => {
    vi.mocked(getPersonas).mockResolvedValue({ default_persona_id: "", personas: [] });

    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(vi.mocked(getPersonas)).toHaveBeenCalled());
    expect(result.current.personaId).toBe(PERSONA);
  });
});

describe("useChatSession 跨界面同步（预设改完要能及时跟上）", () => {
  /** 后端此刻存着的一份偏好（各用例按需覆盖其中几项） */
  function storedPrefs(overrides: Partial<ChatPreferences> = {}): ChatPreferences {
    return {
      persona_id: PERSONA,
      style_id: "stored-style",
      preset_id: "stored-preset",
      st_preset_id: "",
      jailbreak_id: "",
      mode: "",
      user_name: "阿岸",
      ...overrides,
    };
  }

  /** 模拟窗口重新回到前台（桌宠窗长期开着，这是它感知「别处改过」的唯一时机） */
  async function refocus() {
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
  }

  it("★ 重新获得焦点时采纳别处改过的文风与预设（不必重开窗口）", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce(storedPrefs());

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.styleId).toBe("stored-style"));

    // 用户在程序控制台里换了一套文风 / 提示词预设 / 酒馆预设
    vi.mocked(getChatPreferences).mockResolvedValueOnce(
      storedPrefs({
        style_id: "console-style",
        preset_id: "console-preset",
        st_preset_id: "tavern-x",
      }),
    );
    await refocus();

    await waitFor(() => expect(result.current.styleId).toBe("console-style"));
    expect(result.current.presetId).toBe("console-preset");
    expect(result.current.stPresetId).toBe("tavern-x");
  });

  it("★ 别处把预设改回「自动」（空串）同样要传过来", async () => {
    // 空串是有意义的取值：漏掉它就等于「在控制台改回自动」永远不生效
    vi.mocked(getChatPreferences).mockResolvedValueOnce(
      storedPrefs({ preset_id: "manual", st_preset_id: "tavern-x" }),
    );

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.presetId).toBe("manual"));

    vi.mocked(getChatPreferences).mockResolvedValueOnce(
      storedPrefs({ preset_id: "", st_preset_id: "" }),
    );
    await refocus();

    await waitFor(() => expect(result.current.presetId).toBe(""));
    expect(result.current.stPresetId).toBe("");
  });

  it("本窗口自己刚改过的选择不会被回读打回", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce(storedPrefs());

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.styleId).toBe("stored-style"));

    act(() => result.current.setStyleId("mine"));
    await waitFor(() =>
      expect(setChatPreferences).toHaveBeenCalledWith({ style_id: "mine" }),
    );

    // 后端已经存下这次选择；重新获得焦点时读回来的就是它，界面不该有变化
    vi.mocked(getChatPreferences).mockResolvedValueOnce(
      storedPrefs({ style_id: "mine" }),
    );
    await refocus();

    expect(result.current.styleId).toBe("mine");
  });

  it("人设与交互模式不跟着焦点同步（换它们会清空眼前的对话）", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce(storedPrefs());

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.styleId).toBe("stored-style"));
    expect(result.current.personaId).toBe(PERSONA);

    vi.mocked(getChatPreferences).mockResolvedValueOnce(
      storedPrefs({ persona_id: "another-persona", mode: MODE_TAVERN }),
    );
    await refocus();

    // 焦点事件只同步「作用于下一轮」的几项；人设与模式要等下次启动
    expect(result.current.personaId).toBe(PERSONA);
    expect(result.current.mode).toBe("");
  });

  it("后端读不到偏好时保持现状（不为一次离线把用户的选择清掉）", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce(storedPrefs());

    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.styleId).toBe("stored-style"));

    vi.mocked(getChatPreferences).mockResolvedValueOnce(null);
    await refocus();

    expect(result.current.styleId).toBe("stored-style");
  });
});

/**
 * 叙事框架（jailbreak）层：**用户自己决定开不开**。
 *
 * 这几条都不能只靠界面测试守，因为出错时界面看不出异常：
 * - 没选过时必须传 `null`，而不是把界面算出的「有效值」发出去——否则部署默认
 *   会被冻进请求，部署方日后改默认再也传不过来；
 * - `"none"`（用户明确关掉）必须原样透传，被 `||` 之类吃掉变成 `null` 就等于
 *   把开关重新交回给部署默认，用户亲手关掉的动作白做；
 * - 后端存着 `"none"` 时挂载要采纳它，不能当成「未设置」。
 */
describe("useChatSession 叙事框架（破限）层", () => {
  /** 后端此刻存着的一份偏好（默认「没选过叙事框架」） */
  function prefs(overrides: Partial<ChatPreferences> = {}): ChatPreferences {
    return {
      persona_id: PERSONA,
      style_id: "stored-style",
      preset_id: "",
      st_preset_id: "",
      jailbreak_id: "",
      mode: "",
      user_name: "阿岸",
      ...overrides,
    };
  }

  /** 模拟窗口重新回到前台（跨界面同步的唯一触发时机） */
  async function refocus() {
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
  }

  it("默认未设置：请求传 null，交给后端按部署默认解析", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse());
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    expect(result.current.jailbreakId).toBe("");

    await act(async () => {
      await result.current.send("你好");
    });

    expect(vi.mocked(postChat).mock.calls[0][0]).toMatchObject({
      jailbreak_id: null,
    });
  });

  it("★ 用户开启后请求带上该档，并写进共享偏好", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse());
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    act(() => result.current.setJailbreakId("immersive-narrative"));
    await act(async () => {
      await result.current.send("你好");
    });

    expect(vi.mocked(postChat).mock.calls[0][0]).toMatchObject({
      jailbreak_id: "immersive-narrative",
    });
    // 写的是共享偏好：三个界面（控制台 / Web 端 / 桌宠窗）看到同一份选择
    expect(vi.mocked(setChatPreferences)).toHaveBeenCalledWith({
      jailbreak_id: "immersive-narrative",
    });
  });

  it("★ 用户明确关掉时传 \"none\"（不能被当成「未设置」退回部署默认）", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse());
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    act(() => result.current.setJailbreakId("none"));
    await act(async () => {
      await result.current.send("你好");
    });

    expect(vi.mocked(postChat).mock.calls[0][0]).toMatchObject({
      jailbreak_id: "none",
    });
  });

  it("★ 后端存着 \"none\" 时挂载要采纳（那不是「没选过」）", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce(
      prefs({ jailbreak_id: "none" }),
    );
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.jailbreakId).toBe("none"));
  });

  it("★ 只动过叙事框架开关，不影响人设 / 文风的部署默认校准", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce(
      prefs({
        persona_id: "",
        style_id: "",
        preset_id: "",
        jailbreak_id: "none",
      }),
    );
    const { result } = renderHook(() => useChatSession(fakeAvatar));

    await waitFor(() => expect(result.current.jailbreakId).toBe("none"));
    // `hasStoredPreference` 刻意不含 jailbreak_id：它算进去的话，一个只碰过
    // 破限开关的用户会连带让人设 / 文风的部署默认失效，界面停在写死的初值上
    expect(result.current.hasStoredPreference).toBe(false);
  });

  it("切换叙事框架不切会话、不动记忆（只作用于下一轮）", async () => {
    vi.mocked(postChat).mockResolvedValue(chatResponse());
    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await act(async () => {
      await result.current.send("你好");
    });
    expect(result.current.sessionId).toBe("s-new");

    act(() => result.current.setJailbreakId("mature-fiction"));

    expect(result.current.messages).toHaveLength(2);
    expect(result.current.sessionId).toBe("s-new");
  });

  it("跨界面同步：别处改过的选择会被采纳", async () => {
    vi.mocked(getChatPreferences).mockResolvedValueOnce(prefs());
    const { result } = renderHook(() => useChatSession(fakeAvatar));
    await waitFor(() => expect(result.current.styleId).toBe("stored-style"));

    vi.mocked(getChatPreferences).mockResolvedValueOnce(
      prefs({ jailbreak_id: "strict-in-character" }),
    );
    await refocus();

    expect(result.current.jailbreakId).toBe("strict-in-character");
  });
});
