"use client";

/**
 * 对话会话编排：串起「具身状态机 + 后端请求 + 播报 + 情绪视觉 + 会话持久化」。
 *
 * 流程（与赛题要求的 Listen/Think/Speak/Interrupt 对齐）：
 *   listen（记录用户输入）→ think（等待后端）→ speak（播报 + 字幕）→ idle
 *
 * 会话持久化（刷新页面不丢）：
 * - 消息的唯一事实来源是**后端**（会话落 SQLite），前端只在 localStorage 记
 *   「当前会话 id」；刷新后用该 id 向后端拉历史，界面与模型上下文因此始终一致
 *   （前端若自行缓存消息，会出现「界面有 10 轮、模型只看得到 1 轮」的割裂）。
 * - 会话指针按陪伴对象分开，切换角色各自恢复各自的会话。
 *
 * 稳定性设计：
 * - `turnRef` 回合序号：发送与打断都会自增，旧回合的后续写入全部失效
 *   （否则打断后旧回合会把新回合状态打回 idle，且第二次「打断」静默失效）；
 * - `restoreSeqRef` 历史请求序号：切换会话/新建后，在途的旧响应会被丢弃；
 * - `avatar` / `busy` / `streamingSpeech` 经 ref 读取，使 send / interrupt 身份稳定；
 * - 每条消息带稳定 id，供列表 key 使用（避免用数组下标）。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  deleteSession,
  getChatPreferences,
  getSessionHistory,
  postChat,
  postSpeak,
  setChatPreferences,
  type ChatPreferences,
} from "@/lib/api/client";
import { DEFAULT_PERSONA_ID } from "@/lib/chat/persona";
import { readActiveSessionId, writeActiveSessionId } from "@/lib/chat/session-store";
import type { ChatMessage, EmotionInfo, MemoryCounts, ToolUsage } from "@/lib/api/types";
import type { AvatarController, SpeakContext, SpeechChunk } from "@/hooks/avatar/useAvatar";

/**
 * 取流式分段（逐段纯文本 + SSML）。
 *
 * 通过 `POST /media/speak` 拿分段：SSML 由后端生成（含 XML 转义与 KA 结构），
 * 前端不自行拼标签。
 *
 * 失败时返回空数组 → 调用方回退整段播报：分段只是体验优化，
 * 不该因为它失败而让整段播报也播不出来。
 */
/**
 * 分段播报的**单段字数上限**。
 *
 * 为什么是 20：实测合成耗时随字数近似线性增长（15 字 ≈ 3.0s、46 字 ≈ 8.8s），
 * 所以首段越短出声越早；但切得太碎又会在句子中间硬切（语调不自然）。
 * 20 字刚好让常见的中文句子（15~17 字）自然成段，不触发硬切。
 */
const SEGMENT_MAX_CHARS = 20;

async function fetchSpeechChunks(
  reply: string,
  emotion: string | undefined,
  intensity: number | undefined,
  signal: AbortSignal,
): Promise<SpeechChunk[]> {
  try {
    const command = await postSpeak(
      {
        text: reply,
        emotion: emotion ?? null,
        intensity: intensity ?? 0.5,
        streaming: true,
        maxChars: SEGMENT_MAX_CHARS,
      },
      { signal },
    );
    const texts = command.chunks ?? [];
    const ssmls = command.ssml_chunks ?? [];
    // 两侧必须一一对应，否则无法确定某段字幕该配哪段 SSML
    if (texts.length === 0 || texts.length !== ssmls.length) return [];
    return texts.map((text, index) => ({ text, ssml: ssmls[index] }));
  } catch {
    return [];
  }
}

export interface ChatSession {
  messages: ChatMessage[];
  emotion: EmotionInfo | null;
  toolsUsed: ToolUsage[];
  subtitle: string;
  tone: string;
  memoryCounts: MemoryCounts;
  /** 本轮个人记忆（知识库）召回条数 */
  knowledgeHits: number;
  /** 本轮命中的世界书条目 id */
  worldbookHits: string[];
  /** 本轮记忆写入是否已提交后台 */
  memoryScheduled: boolean;
  /** 系统提示词估算 token */
  estimatedTokens: number;
  /** 后端告警（如某层召回降级） */
  warnings: string[];
  sessionId: string | null;
  /** 正在从后端恢复历史（首屏 / 打开历史会话） */
  restoring: boolean;
  /** 历史恢复失败的提示（不阻断继续对话） */
  historyError: string | null;
  /** 会话数据变化计数：外部（历史列表）据此知道该刷新了 */
  sessionVersion: number;
  /** 当前陪伴对象（人设）id；同时是个人记忆的命名空间 companion_id */
  personaId: string;
  /** 切换陪伴对象：会切到该角色自己的会话（记忆按对象隔离） */
  setPersonaId: (id: string) => void;
  /** 新建对话：清空界面与本地指针；历史仍留在「历史记录」里 */
  newSession: () => void;
  /** 打开某个历史会话（从列表点选） */
  openSession: (sessionId: string) => Promise<void>;
  /** 删除一段对话（不可恢复）；删的是当前会话时会回到新对话 */
  removeSession: (sessionId: string) => Promise<void>;
  /** 重试历史恢复（恢复失败时用户点「重试」） */
  retryHistory: () => Promise<void>;
  styleId: string;
  setStyleId: (id: string) => void;
  /** 模型预设 id；"" = 自动（后端按模型名匹配） */
  presetId: string;
  setPresetId: (id: string) => void;
  /** 已导入的酒馆预设 id；"" = 不使用（走内置分层组装） */
  stPresetId: string;
  setStPresetId: (id: string) => void;
  /** 后端是否已存过偏好（页面据此决定要不要按部署默认校准） */
  hasStoredPreference: boolean;
  /** 本轮 ST 组装的元信息（命中/空槽位/未识别宏等；走内置路径时为 {}） */
  stPresetMeta: Record<string, unknown>;
  /**
   * 分段播报开关（默认开）：逐段播报，首段更早出声、字幕随语音同步。
   * 魔珐 SDK 路径下段间有约 400ms 过渡，想整段连贯可关。
   */
  streamingSpeech: boolean;
  setStreamingSpeech: (on: boolean) => void;
  busy: boolean;
  error: string | null;
  send: (text: string) => Promise<void>;
  interrupt: () => void;
}

export function useChatSession(avatar: AvatarController, userName = "小林"): ChatSession {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [emotion, setEmotion] = useState<EmotionInfo | null>(null);
  const [toolsUsed, setToolsUsed] = useState<ToolUsage[]>([]);
  const [subtitle, setSubtitle] = useState("");
  const [tone, setTone] = useState("");
  const [memoryCounts, setMemoryCounts] = useState<MemoryCounts>({});
  const [knowledgeHits, setKnowledgeHits] = useState(0);
  const [worldbookHits, setWorldbookHits] = useState<string[]>([]);
  const [memoryScheduled, setMemoryScheduled] = useState(false);
  const [estimatedTokens, setEstimatedTokens] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [sessionVersion, setSessionVersion] = useState(0);
  const [personaId, setPersonaId] = useState(DEFAULT_PERSONA_ID);
  const [styleId, setStyleId] = useState("modern-conversational");
  const [presetId, setPresetId] = useState(""); // "" = 自动（按模型名匹配）
  const [stPresetId, setStPresetId] = useState(""); // "" = 不使用酒馆预设
  /**
   * 后端是否已存过对话偏好。
   *
   * 页面据此决定「还要不要按部署默认校准」：用户上次的选择优先于部署声明，
   * 否则控制台里改好的偏好会被页面自己的校准逻辑覆盖回去。
   */
  const [hasStoredPreference, setHasStoredPreference] = useState(false);
  const [stPresetMeta, setStPresetMeta] = useState<Record<string, unknown>>({});
  /**
   * 分段播报开关：**默认开**。
   *
   * 服务端 TTS 下它就是"流水线"（首段 2~3s 出声、字幕随语音同步、段间无缝），
   * 没有理由默认关；魔珐 SDK 路径下段间会有 400ms 的 interactive_idle 过渡，
   * 想要整段连贯的可以在这里关掉。
   */
  const [streamingSpeech, setStreamingSpeech] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  /** 回合序号：发送与打断都会自增；旧回合据此判断自己已被作废 */
  const turnRef = useRef(0);
  /** 历史请求序号：切换会话后旧响应必须被丢弃（否则会把上一个会话的消息写进来） */
  const restoreSeqRef = useRef(0);
  /** 消息 id 自增计数（仅用于前端列表 key） */
  const messageIdRef = useRef(0);

  // avatar / busy / 当前陪伴对象经 ref 读取：让 send 等回调身份保持稳定
  const avatarRef = useRef(avatar);
  useEffect(() => {
    avatarRef.current = avatar;
  }, [avatar]);
  const busyRef = useRef(busy);
  busyRef.current = busy;
  /** 分段播报开关经 ref 读取：send 的身份保持稳定，不必随开关重建 */
  const streamingRef = useRef(streamingSpeech);
  streamingRef.current = streamingSpeech;
  const personaIdRef = useRef(personaId);
  personaIdRef.current = personaId;

  /*
   * 挂载后拉一次后端偏好（人设 / 文风 / 预设 / 酒馆预设）。
   *
   * 它是**三个界面共享**的一份选择（控制台、Web 端、桌宠窗的 origin 不同，
   * localStorage 不共享）。空串 = 没选过，此时保持 hook 的初始值，
   * 让页面按后端的部署默认去校准。
   */
  useEffect(() => {
    let active = true;

    void getChatPreferences().then((prefs) => {
      // null = 后端读不到：保持界面现有值，没必须把它当成「未设置」
      if (!active || !prefs) return;

      if (prefs.persona_id) setPersonaId(prefs.persona_id);
      if (prefs.style_id) setStyleId(prefs.style_id);
      if (prefs.preset_id) setPresetId(prefs.preset_id);
      if (prefs.st_preset_id) setStPresetId(prefs.st_preset_id);

      setHasStoredPreference(
        Boolean(
          prefs.persona_id || prefs.style_id || prefs.preset_id || prefs.st_preset_id,
        ),
      );
    });

    return () => {
      active = false;
    };
  }, []);
  /** 当前会话 id 的同步引用（供异步回调判断「删的是不是当前会话」） */
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  /** 清空与本轮对话相关的界面状态（新建 / 打开会话共用） */
  const resetTurnState = useCallback(() => {
    setSubtitle("");
    setEmotion(null);
    setTone("");
    setMemoryCounts({});
    setKnowledgeHits(0);
    setWorldbookHits([]);
    setMemoryScheduled(false);
    setEstimatedTokens(0);
    setWarnings([]);
    setToolsUsed([]);
    setStPresetMeta({});
    setError(null);
    setHistoryError(null);
  }, []);

  /**
   * 从后端拉取历史并替换界面消息（刷新恢复 / 打开历史会话共用一条路径）。
   */
  const applyHistory = useCallback(async (targetSessionId: string, targetPersonaId: string) => {
    const seq = ++restoreSeqRef.current;
    setRestoring(true);
    try {
      const history = await getSessionHistory(targetSessionId);
      if (seq !== restoreSeqRef.current) return; // 已被更新的请求取代 → 丢弃
      // 后端会话的陪伴对象与当前不符（如本地指针过期）→ 不恢复，退回新对话
      if (history.persona_id !== targetPersonaId) {
        writeActiveSessionId(targetPersonaId, null);
        setSessionId(null);
        setMessages([]);
        return;
      }
      setSessionId(history.session_id);
      setMessages(
        history.messages
          .filter((message) => message.role === "user" || message.role === "assistant")
          .map((message, index) => ({
            id: `hist-${index}`,
            role: message.role as "user" | "assistant",
            text: message.text,
          })),
      );
      setHistoryError(null);
    } catch (err) {
      if (seq !== restoreSeqRef.current) return;
      // 会话不存在（后端换了库 / 手动清理）→ 当作新对话，而不是报错挡住界面
      if (err instanceof ApiError && err.status === 404) {
        writeActiveSessionId(targetPersonaId, null);
        setSessionId(null);
        setMessages([]);
      } else {
        setHistoryError(err instanceof Error ? err.message : "读取历史失败");
      }
    } finally {
      if (seq === restoreSeqRef.current) setRestoring(false);
    }
  }, []);

  // 陪伴对象变化（含首屏）→ 恢复该角色自己的会话
  useEffect(() => {
    const stored = readActiveSessionId(personaId);
    if (!stored) {
      // 该角色还没有会话指针：确保界面是干净的新对话
      restoreSeqRef.current += 1;
      setSessionId(null);
      setMessages([]);
      setRestoring(false);
      return;
    }
    void applyHistory(stored, personaId);
  }, [personaId, applyHistory]);

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || busyRef.current) return;

      const turn = ++turnRef.current;
      /** 本回合是否仍是当前回合（被打断 / 被新回合取代后为 false） */
      const isCurrent = () => turnRef.current === turn;
      const currentAvatar = avatarRef.current;

      // 作废在途的历史恢复：它返回时会用 setMessages **整体替换**消息数组，
      // 把刚追加的用户气泡抹掉（触发路径：刷新页面后、历史还没回来就发消息）
      restoreSeqRef.current += 1;
      setRestoring(false);

      setError(null);
      setBusy(true);
      currentAvatar.setState("listen"); // ① 聆听
      setMessages((prev) => [
        ...prev,
        { id: `msg-${++messageIdRef.current}`, role: "user", text: content },
      ]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        currentAvatar.setState("think"); // ② 思考（等待后端）
        const res = await postChat(
          {
            text: content,
            session_id: sessionId,
            persona_id: personaId,
            style_id: styleId,
            user_name: userName,
            preset_id: presetId || null,
            st_preset_id: stPresetId || null,
          },
          { signal: controller.signal },
        );
        // 已被打断 / 被新回合取代：丢弃结果，不写任何界面状态
        if (!isCurrent()) return;

        setSessionId(res.session_id);
        // 记住会话指针：刷新后据此恢复；同时通知历史列表刷新
        writeActiveSessionId(personaId, res.session_id);
        setSessionVersion((version) => version + 1);
        setMessages((prev) => [
          ...prev,
          { id: `msg-${++messageIdRef.current}`, role: "assistant", text: res.reply },
        ]);
        setEmotion(res.emotion ?? null);
        setToolsUsed(res.tools_used ?? []);
        // 字幕**交给播报驱动**（见下面的 onChunk / speak 之前那行）：
        // 若在这里就显示整段，就会出现"文字先到、声音后到"——正是要消除的体感。
        setSubtitle("");
        setTone(res.speak?.tone ?? "");
        setMemoryCounts(res.memory_counts ?? {});
        setKnowledgeHits(res.knowledge_hits ?? 0);
        setWorldbookHits(res.worldbook_hits ?? []);
        setMemoryScheduled(Boolean(res.memory_scheduled));
        setEstimatedTokens(res.estimated_tokens ?? 0);
        setWarnings(res.warnings ?? []);
        setStPresetMeta(res.st_preset ?? {});

        currentAvatar.setState("speak"); // ③ 播报（文本给浏览器 TTS，SSML 给魔珐 SDK）
        // 播报上下文：服务端 TTS 需要本轮情绪（它要用情绪换后端的表情时间轴与语气时长）；
        // 浏览器 TTS 与魔珐 SDK 忽略它（后者的 KA 动作已在 SSML 里）。
        const speakContext: SpeakContext = {
          emotion: res.emotion?.label ?? null,
          intensity: res.emotion?.intensity ?? 0.5,
        };
        // 分段播报：多一次 /media/speak 请求换取「逐段产出」——服务端 TTS 下每段独立合成，
        // 首段先出声且字幕与语音同步；取不到分段（短回复 / 请求失败）时自然回退整段播报。
        let chunks: SpeechChunk[] = [];
        if (streamingRef.current) {
          chunks = await fetchSpeechChunks(
            res.reply,
            res.emotion?.label,
            res.emotion?.intensity,
            controller.signal,
          );
          if (!isCurrent()) return;
        }
        const speakText = res.speak?.display_text || res.reply;
        if (chunks.length > 1) {
          // 流水线播报：字幕由 onChunk 在**每段音频就绪那一刻**驱动 → 字声同时出现
          await currentAvatar.speakChunks(
            chunks,
            (index) => {
              if (isCurrent()) setSubtitle(chunks[index].text);
            },
            speakContext,
          );
        } else {
          // 整段播报（短回复）：字幕与音频几乎同时（这行就在 speak 之前）
          setSubtitle(speakText);
          await currentAvatar.speak(speakText, res.speak?.ssml, speakContext);
        }
        if (!isCurrent()) return; // 播报期间被打断 → 状态已由 interrupt() 处理
        currentAvatar.setState("idle"); // ④ 回到待机
      } catch (err) {
        if (!isCurrent()) return; // 旧回合的失败不得影响新回合界面
        if ((err as Error).name === "AbortError") {
          // 打断属用户主动行为：interrupt() 已清掉提示，这里不再当错误展示
          currentAvatar.setState("idle");
        } else {
          setError((err as Error).message || "对话失败，请检查后端是否已启动。");
          currentAvatar.setState("idle");
        }
      } finally {
        // 只清理本回合：否则旧回合会把新回合的 controller 清空，
        // 导致第二次「打断」静默失效
        if (isCurrent()) {
          setBusy(false);
          abortRef.current = null;
        }
      }
    },
    [sessionId, personaId, styleId, presetId, stPresetId, userName],
  );

  /** 中止进行中的回合（切换会话 / 切换角色 / 打断共用） */
  const abortCurrentTurn = useCallback(() => {
    turnRef.current += 1; // 作废进行中的回合（其后续 setState 全部失效）
    abortRef.current?.abort();
    abortRef.current = null;
    avatarRef.current.interrupt();
    setBusy(false);
  }, []);

  /**
   * 切换陪伴对象。
   *
   * 人设即记忆命名空间（companion_id）：不复用上一个角色的 session_id，
   * 改由恢复 effect 按新角色去取「它自己的会话」（可能是新对话）。
   */
  /**
   * 记住用户这次的选择（后端那一份是三界面共享的）。
   *
   * 失败**不影响本次使用**：存不上顶多是下次打开回到旧的选择，
   * 为它弹错误反而更吵。
   */
  const rememberPreference = useCallback((patch: Partial<ChatPreferences>) => {
    setHasStoredPreference(true);
    void setChatPreferences(patch).catch(() => undefined);
  }, []);

  const changePersona = useCallback(
    (id: string) => {
      abortCurrentTurn();
      resetTurnState();
      setPersonaId(id); // 恢复 effect 会按新角色装载会话
      rememberPreference({ persona_id: id });
    },
    [abortCurrentTurn, resetTurnState, rememberPreference],
  );

  const changeStyle = useCallback(
    (id: string) => {
      setStyleId(id);
      rememberPreference({ style_id: id });
    },
    [rememberPreference],
  );

  const changePreset = useCallback(
    (id: string) => {
      setPresetId(id);
      rememberPreference({ preset_id: id });
    },
    [rememberPreference],
  );

  const changeStPreset = useCallback(
    (id: string) => {
      setStPresetId(id);
      rememberPreference({ st_preset_id: id });
    },
    [rememberPreference],
  );

  /**
   * 清空当前会话的界面与本地指针（新建对话 / 删除当前对话共用）。
   *
   * 指针必须一起清：否则刷新后会拿着一个已不存在（或被删）的会话 id
   * 去请求历史，白白多一轮 404。
   */
  const clearActiveSession = useCallback(() => {
    abortCurrentTurn();
    restoreSeqRef.current += 1; // 作废在途的历史请求
    writeActiveSessionId(personaIdRef.current, null);
    setSessionId(null);
    setMessages([]);
    resetTurnState();
    setRestoring(false);
  }, [abortCurrentTurn, resetTurnState]);

  /** 新建对话：清空指针与界面（历史仍保留在后端，可在「历史记录」里找回） */
  const newSession = useCallback(() => {
    clearActiveSession();
    setSessionVersion((version) => version + 1);
  }, [clearActiveSession]);

  /**
   * 删除一段对话。
   *
   * 若删的正是当前会话，界面要回到干净的新对话（含清掉本地指针）；
   * 无论删除的是哪一段，都通知历史列表刷新。
   */
  const removeSession = useCallback(
    async (targetSessionId: string) => {
      await deleteSession(targetSessionId, personaIdRef.current);
      if (sessionIdRef.current === targetSessionId) {
        clearActiveSession();
      }
      setSessionVersion((version) => version + 1);
    },
    [clearActiveSession],
  );

  /** 打开历史会话（列表点选） */
  const openSession = useCallback(
    async (targetSessionId: string) => {
      abortCurrentTurn();
      resetTurnState();
      // 先把会话指针切到目标：否则恢复期间发消息会落到**上一个**会话
      // （随后到达的历史响应还会把界面覆盖成目标会话，造成两段对话混排）
      setSessionId(targetSessionId);
      writeActiveSessionId(personaIdRef.current, targetSessionId);
      await applyHistory(targetSessionId, personaIdRef.current);
    },
    [abortCurrentTurn, applyHistory, resetTurnState],
  );

  /**
   * 重试历史恢复。
   *
   * 历史读取失败时，界面是空的而本地指针仍在；若用户就这么发消息，
   * 后端会新建会话并覆盖指针（原对话只能去列表里找回）。因此提供显式重试。
   */
  const retryHistory = useCallback(async () => {
    const stored = readActiveSessionId(personaIdRef.current);
    setHistoryError(null);
    if (!stored) {
      setRestoring(false);
      return;
    }
    await applyHistory(stored, personaIdRef.current);
  }, [applyHistory]);

  /** 打断：立即停止播报并中止请求（客户端即时打断，不等服务端）。 */
  const interrupt = useCallback(() => {
    turnRef.current += 1; // 作废进行中的回合（其后续 setState 全部失效）
    abortRef.current?.abort();
    abortRef.current = null;
    avatarRef.current.interrupt();
    setBusy(false);
    setError(null); // 打断是用户主动行为，不作为错误提示
  }, []);

  return {
    messages,
    emotion,
    toolsUsed,
    subtitle,
    tone,
    memoryCounts,
    knowledgeHits,
    worldbookHits,
    memoryScheduled,
    estimatedTokens,
    warnings,
    sessionId,
    restoring,
    historyError,
    sessionVersion,
    personaId,
    setPersonaId: changePersona,
    newSession,
    openSession,
    removeSession,
    retryHistory,
    styleId,
    setStyleId: changeStyle,
    presetId,
    setPresetId: changePreset,
    stPresetId,
    setStPresetId: changeStPreset,
    hasStoredPreference,
    stPresetMeta,
    streamingSpeech,
    setStreamingSpeech,
    busy,
    error,
    send,
    interrupt,
  };
}
