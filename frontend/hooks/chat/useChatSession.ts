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
  deleteSessions,
  getChatPreferences,
  getPersonas,
  getSessionHistory,
  postChat,
  postSpeak,
  setChatPreferences,
  type ChatPreferences,
} from "@/lib/api/client";
import {
  subscribeEvents,
  type EventChannelState,
  type ProactiveMessagePayload,
} from "@/lib/api/events";
import { DEFAULT_PERSONA_ID } from "@/lib/chat/persona";
import { readActiveSessionId, writeActiveSessionId } from "@/lib/chat/session-store";
import { DEFAULT_USER_NAME, normalizeUserName } from "@/lib/chat/user";
import type {
  ChatMessage,
  EmotionInfo,
  MemoryCounts,
  PersonaCatalog,
  SpeakCommand,
  ToolUsage,
} from "@/lib/api/types";
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

/**
 * 从人设清单里挑一个**可用**的 id（校正失效角色时用）。
 *
 * 优先级与后端/控制台一致：部署声明的默认 > 清单首项。清单为空时返回空串，
 * 调用方据此放弃校正——宁可把错误原样报给用户，也不发一个空 id 出去。
 */
function pickFallbackPersona(catalog: PersonaCatalog | null): { id: string; name: string } {
  const personas = catalog?.personas ?? [];
  const ids = personas.map((item) => item.id);
  const declared = catalog?.default_persona_id ?? "";
  const id = declared && ids.includes(declared) ? declared : (ids[0] ?? "");
  return { id, name: personas.find((item) => item.id === id)?.name ?? id };
}

/**
 * 是否为「后端不认识这个人设」的错误（404 / 未知人设）。
 *
 * 只认这一类 404，不把所有 404 都当人设问题：会话不存在（换过库、手动清理过）
 * 同样返回 404，那种情况该按新对话处理，不该顺手换掉用户选的角色。
 */
function isUnknownPersona(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404 && err.message.includes("人设");
}

async function fetchSpeechChunks(
  reply: string,
  emotion: string | undefined,
  intensity: number | undefined,
  signal?: AbortSignal,
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
  /**
   * 用户选定的交互模式（`companion` 桌宠 / `tavern` 酒馆）。
   *
   * 空串 = 没选过，由后端按 ST 预设推断。它是**会话级**选择：切换会开始新对话。
   */
  mode: string;
  /** 切换交互模式（清空当前会话界面；历史仍可在「历史记录」里找回） */
  setMode: (mode: string) => void;
  /**
   * 后端**实际生效**的模式（面板展示用，不参与请求）。
   *
   * 与 `mode` 可能不同：没选过时后端会按 ST 预设推断。空串 = 还没聊过。
   */
  effectiveMode: string;
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
  /** 清空当前陪伴对象的全部会话（不可恢复）；清完回到新对话 */
  clearAllSessions: () => Promise<void>;
  /** 重试历史恢复（恢复失败时用户点「重试」） */
  retryHistory: () => Promise<void>;
  styleId: string;
  setStyleId: (id: string) => void;
  /**
   * 叙事框架（jailbreak）选择。**空串与 `"none"` 语义不同**：
   * - `""`     没选过 → 跟随部署默认（出厂关闭）
   * - `"none"` 用户明确关掉 → 即使部署默认开启也不启用
   * - 具体 id → 启用该档
   *
   * 界面（`JailbreakSwitcher`）负责把「未设置」渲染成真实生效的那一档。
   */
  jailbreakId: string;
  setJailbreakId: (id: string) => void;
  /** 模型预设 id；"" = 自动（后端按模型名匹配） */
  presetId: string;
  setPresetId: (id: string) => void;
  /** 已导入的酒馆预设 id；"" = 不使用（走内置分层组装） */
  stPresetId: string;
  setStPresetId: (id: string) => void;
  /**
   * 用户自己的称呼（对话里 `{{user_name}}` 的取值）。
   * 未设置过时是默认值「朋友」——不是某个具体人名的兜底。
   */
  userName: string;
  /** 改用户称呼（写共享偏好；下一轮用新称呼，不切会话） */
  setUserName: (name: string) => Promise<void>;
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
  /**
   * 主动消息通道（SSE）状态。
   *
   * 见 `docs/proactive-multimodal.md` §5.1——主动沟通的**物理前提**是服务端能推送，
   * 所以这个状态本身就是「它会自己开口吗」的答案。
   * `offline` = 浏览器不支持 / 后端未启动 / 还没确定角色。
   */
  eventChannel: EventChannelState;
  /**
   * 最近一次「被节制层拦下」的原因。
   *
   * **可观测性专用**（调参靠它，而不是靠猜「为什么它不说话」）。
   * 默认不展示——用户不需要知道系统内部拦了什么，排查时再看。
   */
  proactiveNotice: string;
  /** 感知提示摘要（如「用户正在听《起风了》」）；没有感知源时为空串 */
  perceptionHint: string;
  /** 本次页面会话内主动开口的次数（给用户一个「它会主动找我」的反馈） */
  proactiveCount: number;
}

/**
 * 对话会话编排。
 *
 * 人设 / 文风 / 预设与**用户称呼**都从后端共享偏好读（挂载后拉一次），
 * 所以 hook 不再接收「我叫什么」作为参数：写死一个默认人名的那套已经去掉了。
 */
export function useChatSession(avatar: AvatarController): ChatSession {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [emotion, setEmotion] = useState<EmotionInfo | null>(null);
  const [toolsUsed, setToolsUsed] = useState<ToolUsage[]>([]);
  const [subtitle, setSubtitle] = useState("");
  const [tone, setTone] = useState("");
  const [memoryCounts, setMemoryCounts] = useState<MemoryCounts>({});
  /** 用户选定的交互模式（空串 = 没选过，交给后端按 ST 预设推断） */
  const [mode, setModeValue] = useState("");
  /** 后端实际生效的模式（只用于展示，不参与请求） */
  const [effectiveMode, setEffectiveMode] = useState("");
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
  /**
   * 叙事框架层：初值 `""`（未设置）而不是 `"none"`。
   *
   * 区别在**要不要替用户表态**：`""` 会让界面回落到部署声明的默认，
   * 而 `"none"` 是「用户明确关掉」。初值用后者就等于替所有用户做了这个决定，
   * 部署方日后把默认改成开启也传不到界面。
   */
  const [jailbreakId, setJailbreakId] = useState("");
  const [presetId, setPresetId] = useState(""); // "" = 自动（按模型名匹配）
  const [stPresetId, setStPresetId] = useState(""); // "" = 不使用酒馆预设
  /** 用户称呼：初值用默认，偏好拉回后覆盖（空偏好保持默认） */
  const [userName, setUserName] = useState(DEFAULT_USER_NAME);
  /**
   * 后端是否已存过对话偏好。
   *
   * 页面据此决定「还要不要按部署默认校准」：用户上次的选择优先于部署声明，
   * 否则控制台里改好的偏好会被页面自己的校准逻辑覆盖回去。
   *
   * 叙事框架（`jailbreak_id`）**刻意不算在内**：它是个独立开关，用户碰它
   * 不代表他选过人设 / 文风。算进来的话，一个只动过破限开关的用户会连带
   * 让人设 / 文风的部署默认失效，界面停在 hook 里写死的初值上。
   */
  const [hasStoredPreference, setHasStoredPreference] = useState(false);
  const [stPresetMeta, setStPresetMeta] = useState<Record<string, unknown>>({});
  /**
   * 分段播报开关：**默认开**。
   *
   * 两条路径都受益：服务端 TTS 下它就是"流水线"（首段 2~3s 出声、字幕随语音同步、
   * 段间无缝）；魔珐 SDK 下走**同一次播报的流式片段**（首段 `is_start`、末段
   * `is_end`，段间不再有 `interactive_idle` 过渡），同样首段早出声且段间无缝。
   */
  const [streamingSpeech, setStreamingSpeech] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 主动消息通道（SSE）状态；见 `lib/api/events.ts` */
  const [eventChannel, setEventChannel] = useState<EventChannelState>("offline");
  /** 最近一次被节制层拦下的原因（可观测性，默认不展示） */
  const [proactiveNotice, setProactiveNotice] = useState("");
  /** 感知提示摘要（如「用户正在听《X》」） */
  const [perceptionHint, setPerceptionHint] = useState("");
  /** 本次页面会话内主动开口的次数 */
  const [proactiveCount, setProactiveCount] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  /** 回合序号：发送与打断都会自增；旧回合据此判断自己已被作废 */
  const turnRef = useRef(0);
  /** 历史请求序号：切换会话后旧响应必须被丢弃（否则会把上一个会话的消息写进来） */
  const restoreSeqRef = useRef(0);
  /** 消息 id 自增计数（仅用于前端列表 key） */
  const messageIdRef = useRef(0);
  /**
   * 后端偏好的**上次已知快照**（读回来或写进去的那一份）。
   *
   * 跨界面同步靠它判断「这一项是不是在别处被改过」：只有与快照不同的值才采纳。
   * 无条件覆盖会把本窗口刚做的选择打回去（写入是乐观的，写失败时本地值与后端
   * 并不一致），而完全不覆盖又会让「在控制台把预设改回自动」传不过来——
   * 后者恰恰是空串这种**有意义**的取值（见下面的同步 effect）。
   */
  const syncedPrefsRef = useRef<ChatPreferences>({
    persona_id: "",
    style_id: "",
    preset_id: "",
    st_preset_id: "",
    jailbreak_id: "",
    mode: "",
    user_name: "",
  });

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
  /**
   * `changePersona` 的稳定引用。
   *
   * `send` 定义在它前面，catch 里却要用它做「角色已失效 → 换一个可用角色」的
   * 自愈；把它加进 `send` 的依赖会让 send 每次渲染都换身份（下游 effect 跟着重跑），
   * 所以走 ref。
   */
  const changePersonaRef = useRef<(id: string) => void>(() => {});

  /*
   * 挂载后拉一次后端偏好（人设 / 文风 / 预设 / 酒馆预设 / 交互模式）。
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

      // 记下后端此刻的值：跨界面同步据此判断「别处改过没有」（见下面的 focus effect）
      syncedPrefsRef.current = prefs;

      if (prefs.persona_id) setPersonaId(prefs.persona_id);
      if (prefs.style_id) setStyleId(prefs.style_id);
      if (prefs.preset_id) setPresetId(prefs.preset_id);
      if (prefs.st_preset_id) setStPresetId(prefs.st_preset_id);
      if (prefs.mode) setModeValue(prefs.mode);
      if (prefs.user_name) setUserName(prefs.user_name);
      /*
       * 叙事框架**不用真值判断**：`""`（没选过）与 `"none"`（用户明确关掉）
       * 都是合法值，后者一旦被 `if (...)` 滤掉，界面就会回落到部署默认，
       * 把用户亲手关掉的开关重新点亮。原样采纳即可。
       */
      setJailbreakId(prefs.jailbreak_id ?? "");

      setHasStoredPreference(
        Boolean(
          prefs.persona_id ||
            prefs.style_id ||
            prefs.preset_id ||
            prefs.st_preset_id ||
            prefs.mode,
        ),
      );
    });

    return () => {
      active = false;
    };
  }, []);

  /**
   * 跨界面同步：窗口重新获得焦点 / 重新可见时，把**别处改过的预设**拉过来。
   *
   * 为什么必须有它：三端（Web 端 / 程序控制台 / 桌宠窗）共用后端那一份偏好，
   * 但控制台的面板自己也写着「**正在运行的**对话界面不会被就地改写，下次打开才生效」。
   * 对 Web 端这没问题（它本来就是个页面），可**桌宠窗是长期开着的那一个窗口**——
   * 「下次打开」可能是几天以后：用户在控制台换了文风，回到桌宠窗接着聊，
   * 听到的还是旧的语气，只会觉得「改了没用」。这正是「无法及时切换预设」的根因。
   *
   * 只同步**不影响会话语义**的五项（文风 / 提示词预设 / 酒馆预设 / 叙事框架 /
   * 用户称呼）：它们作用于下一轮，采纳了不会打断或清空眼前的对话，也不会挪动记忆。
   * 人设与交互模式**刻意不同步**——换人设等于换一整套记忆命名空间（`companion:{id}`），
   * 换模式会开一段新会话，拿一次焦点事件就把用户眼前的对话清掉是不可接受的；
   * 那两项仍然只在下次启动时生效（与控制台面板的说明一致）。
   *
   * 用「与上次已知快照不同才采纳」而不是「非空才采纳」：`preset_id` 与
   * `st_preset_id` 的**空串是有意义的**（自动 / 不使用），漏掉它就等于
   * 「在控制台把预设改回自动」永远传不过来。
   */
  useEffect(() => {
    function syncSharedPresets() {
      // 窗口被最小化 / 隐藏时不拉：等它真的回到前台再同步，省一次无谓请求
      if (document.visibilityState === "hidden") return;

      void getChatPreferences().then((prefs) => {
        if (!prefs) return; // 后端读不到 → 保持现状（与挂载时同一条规则）
        const known = syncedPrefsRef.current;
        if (prefs.style_id !== known.style_id) setStyleId(prefs.style_id);
        if (prefs.preset_id !== known.preset_id) setPresetId(prefs.preset_id);
        if (prefs.st_preset_id !== known.st_preset_id) {
          setStPresetId(prefs.st_preset_id);
        }
        // 叙事框架与文风同类：只作用于下一轮，不会打断或清空眼前的对话。
        // 同样用「与上次快照不同才采纳」——`""` 与 `"none"` 都是有意义的值。
        if (prefs.jailbreak_id !== known.jailbreak_id) {
          setJailbreakId(prefs.jailbreak_id ?? "");
        }
        if (prefs.user_name !== known.user_name) {
          setUserName(prefs.user_name || DEFAULT_USER_NAME);
        }
        syncedPrefsRef.current = prefs;
      });
    }

    window.addEventListener("focus", syncSharedPresets);
    document.addEventListener("visibilitychange", syncSharedPresets);
    return () => {
      window.removeEventListener("focus", syncSharedPresets);
      document.removeEventListener("visibilitychange", syncSharedPresets);
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
    // 只清单轮结果，**不动模式选择**：那是用户的选择，不是这一轮的状态
    setEffectiveMode("");
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

  /**
   * 把一轮回复落到界面并驱动播报。
   *
   * **用户轮与主动轮共用这一条实现**（`docs/proactive-multimodal.md` §5.4）：
   * 主动开口走的是同一条 `chat_graph`，返回的 `reply / emotion / speak` 与
   * `/chat` 响应同构——那么界面上也该只有一处「怎么把回复变成一条消息 + 一段播报」。
   * 两处各写一遍的后果是：改了一处（比如字幕与语音同步的时机），
   * 另一处悄悄退回旧行为，而它只在「角色主动开口」时才复现。
   *
   * 走 ref 而不是直接依赖：`send` 的身份必须稳定（它被 `useCallback` 固定，
   * 依赖变化会让下游的输入框重新挂载）。
   */
  const applyReply = useCallback(
    async (
      payload: { reply: string; emotion?: EmotionInfo | null; speak?: SpeakCommand | null },
      isCurrent: () => boolean,
      signal?: AbortSignal,
    ) => {
      const currentAvatar = avatarRef.current;

      // 空回复不追加空气泡：后端也不落库（见 api/chat.py ⑤），
      // 否则界面会多出一条点开是空的消息，看着像坏了
      if (payload.reply) {
        setMessages((prev) => [
          ...prev,
          { id: `msg-${++messageIdRef.current}`, role: "assistant", text: payload.reply },
        ]);
      }
      setEmotion(payload.emotion ?? null);
      // 字幕**交给播报驱动**（见下面的 onChunk / speak 之前那行）：
      // 若在这里就显示整段，就会出现"文字先到、声音后到"——正是要消除的体感。
      setSubtitle("");
      setTone(payload.speak?.tone ?? "");

      currentAvatar.setState("speak"); // ③ 播报（文本给服务端 TTS，SSML 给魔珐 SDK）
      // 播报上下文：服务端 TTS 需要本轮情绪（它要用情绪换后端的表情时间轴与语气时长）；
      // 魔珐 SDK 忽略它（KA 动作已在 SSML 里），静默路径自然也用不上。
      const speakContext: SpeakContext = {
        emotion: payload.emotion?.label ?? null,
        intensity: payload.emotion?.intensity ?? 0.5,
      };
      // 分段播报：多一次 /media/speak 请求换取「逐段产出」——服务端 TTS 下每段独立合成，
      // 首段先出声且字幕与语音同步；取不到分段（短回复 / 请求失败）时自然回退整段播报。
      //
      // ⚠️ 只有**服务端 TTS** 才分段，两条路径各有一个不能分段的理由：
      // - `none`（未接入 TTS）：没有音频可分段，字幕直接显示整段更稳，也省掉一次
      //   无意义的合成请求；
      // - `xmov`（魔珐 SDK 自带 TTS）：它每段都是一次**独立话轮**，服务端逐段从零
      //   合成 → 段间必有合成空档（用户反馈的「一段一段中间卡壳」）。SDK 的
      //   「同一次播报流式片段」写法实测**完全不发声**（服务端逐条解析 ssml，
      //   见 useAvatar.ts 的 speakChunks 注释）。所以魔珐路径**整段一次播报**——
      //   这是唯一既无缝又出声的路径。
      let chunks: SpeechChunk[] = [];
      if (streamingRef.current && currentAvatar.provider === "server") {
        chunks = await fetchSpeechChunks(
          payload.reply,
          payload.emotion?.label,
          payload.emotion?.intensity,
          signal,
        );
        if (!isCurrent()) return;
      }
      const speakText = payload.speak?.display_text || payload.reply;
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
        await currentAvatar.speak(speakText, payload.speak?.ssml, speakContext);
      }
      if (!isCurrent()) return; // 播报期间被打断 → 状态已由 interrupt() 处理
      currentAvatar.setState("idle"); // ④ 回到待机
    },
    [],
  );

  const applyReplyRef = useRef(applyReply);
  useEffect(() => {
    applyReplyRef.current = applyReply;
  }, [applyReply]);

  /**
   * 待处理的主动消息（用户轮进行中时排队，等这一轮说完再开口）。
   *
   * 为什么不打断用户轮：那会把用户刚问的问题丢在半路。
   * 为什么不并发播报：SDK 的 `speak` 不能连续调用（见 `frontend-avatar-integration.md`
   * 第五节），两段播报同时进行的结果是后一段被静默丢弃——表现成「角色主动说话但没声音」。
   *
   * 后端的闸门⑤（用户刚说过话就不主动）已经拦掉了绝大多数这种情况，
   * 但网络往返存在窗口：闸门通过之后用户才发出消息，这一轮就会撞上。
   */
  const pendingProactiveRef = useRef<ProactiveMessagePayload | null>(null);

  const runProactiveRef = useRef<(payload: ProactiveMessagePayload) => void>(() => {});
  runProactiveRef.current = (payload: ProactiveMessagePayload) => {
    if (busyRef.current) {
      pendingProactiveRef.current = payload;
      return;
    }
    // 主动开口也占用「一个回合」：与用户轮共用同一套回合作废机制
    const turn = ++turnRef.current;
    const isCurrent = () => turnRef.current === turn;
    setBusy(true);
    setSessionId(payload.session_id);
    writeActiveSessionId(personaId, payload.session_id);
    setSessionVersion((version) => version + 1);
    setProactiveCount((count) => count + 1);
    setProactiveNotice("");
    void applyReplyRef
      .current(payload, isCurrent)
      .catch(() => {
        // 播报失败不该让界面卡在「正在说话」：状态复位交给下面的 finally
      })
      .finally(() => {
        if (isCurrent()) setBusy(false);
      });
  };

  // 用户轮结束后，把排队中的主动消息放出来
  useEffect(() => {
    if (busy) return;
    const pending = pendingProactiveRef.current;
    if (!pending) return;
    pendingProactiveRef.current = null;
    runProactiveRef.current(pending);
  }, [busy]);

  /**
   * 订阅主动消息（SSE）。
   *
   * `personaId` 变化时重连——作用域必须跟着角色走，否则切到 B 角色
   * 还会收到 A 角色的话，而那在界面上无法解释（见 `lib/api/events.ts`）。
   *
   * 浏览器不支持 `EventSource` 时静默降级为 `offline`：主动沟通是**增强**，
   * 它不可用不该让对话界面起不来。
   */
  useEffect(() => {
    const unsubscribe = subscribeEvents({
      personaId,
      onStateChange: setEventChannel,
      onPerceptionHint: (payload) => {
        if (payload.summary) setPerceptionHint(payload.summary);
      },
      onProactiveSkipped: (payload) => setProactiveNotice(payload.reason),
      onProactiveMessage: (payload) => runProactiveRef.current(payload),
    });
    return unsubscribe;
  }, [personaId]);

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
            mode: mode || null,
            /*
             * 叙事框架：`""`（没选过）→ 传 `null`，让后端按部署默认解析。
             *
             * 不把界面算出的「有效值」直接发出去：那样会把部署默认**冻**在
             * 用户偏好里，部署方日后改默认就再也传不到这个界面。
             * `"none"` 与具体 id 原样透传，它们都是用户的明确表态。
             */
            jailbreak_id: jailbreakId || null,
          },
          { signal: controller.signal },
        );
        // 已被打断 / 被新回合取代：丢弃结果，不写任何界面状态
        if (!isCurrent()) return;

        setSessionId(res.session_id);
        // 记住会话指针：刷新后据此恢复；同时通知历史列表刷新
        writeActiveSessionId(personaId, res.session_id);
        setSessionVersion((version) => version + 1);
        // 空回复不追加空气泡：后端也不落库（见 api/chat.py ⑤），否则界面会
        // 多出一条点开是空的消息，看着像坏了；改成可读提示，用户可以重试
        if (!res.reply) {
          setError("模型这一轮没有产出内容，请再试一次。");
        }
        setToolsUsed(res.tools_used ?? []);
        setMemoryCounts(res.memory_counts ?? {});
        setEffectiveMode(res.mode ?? "");
        setKnowledgeHits(res.knowledge_hits ?? 0);
        setWorldbookHits(res.worldbook_hits ?? []);
        setMemoryScheduled(Boolean(res.memory_scheduled));
        setEstimatedTokens(res.estimated_tokens ?? 0);
        setWarnings(res.warnings ?? []);
        setStPresetMeta(res.st_preset ?? {});

        // 消息落地 + 情绪 + 播报：与主动开口**共用同一条**实现
        // （见 applyReply 的说明——两处各写一遍，迟早只剩一处是对的）
        await applyReplyRef.current(res, isCurrent, controller.signal);
        if (!isCurrent()) return; // 播报期间被打断 → 状态已由 interrupt() 处理
      } catch (err) {
        if (!isCurrent()) return; // 旧回合的失败不得影响新回合界面
        if ((err as Error).name === "AbortError") {
          // 打断属用户主动行为：interrupt() 已清掉提示，这里不再当错误展示
          currentAvatar.setState("idle");
        } else if (isUnknownPersona(err)) {
          /*
           * 后端不认识当前角色（刚在创作工坊里删了 / 在别的界面删了 /
           * 本地指针来自另一份数据目录）。
           *
           * 不当场换一个的话，用户会一直卡在「每发一句都是 404」；
           * 换角色等于换一整套记忆（人设 id 是记忆命名空间），
           * 所以必须把「换了、为什么换」写在提示里。
           */
          let catalog: PersonaCatalog | null = null;
          try {
            catalog = await getPersonas();
          } catch {
            catalog = null;
          }
          if (!isCurrent()) return;
          const fallback = pickFallbackPersona(catalog);
          if (fallback.id && fallback.id !== personaId) {
            changePersonaRef.current(fallback.id);
            setError(`原来的角色已不存在，已切换到「${fallback.name}」，请重新发送。`);
          } else {
            setError((err as Error).message || "对话失败，请检查后端是否已启动。");
          }
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
    [
      sessionId,
      personaId,
      styleId,
      presetId,
      stPresetId,
      mode,
      userName,
      jailbreakId,
    ],
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
   *
   * 同时**乐观地**更新快照：否则窗口下一次拿到焦点时，跨界面同步会把这次刚做的
   * 选择当成「别处改过的旧值」再写回来一遍（值相同倒也无害，但会让「快照」这个概念
   * 失去意义——它必须始终等于「后端现在应该是多少」）。
   */
  const rememberPreference = useCallback((patch: Partial<ChatPreferences>) => {
    setHasStoredPreference(true);
    syncedPrefsRef.current = { ...syncedPrefsRef.current, ...patch };
    void setChatPreferences(patch).catch(() => undefined);
  }, []);

  const changePersona = useCallback(
    (id: string) => {
      abortCurrentTurn();
      resetTurnState();
      /**
       * 先**立刻**清空界面，再切。
       *
       * 不能只依赖 personaId 的恢复 effect：那个 effect 要等新历史拉回来才整体
       * 替换 messages，中间这段时间屏幕上显示的是**上一个角色的对话**；
       * 而历史请求失败时（catch 里只设 historyError）旧内容会一直留着——
       * 现场看起来就是「换了角色，还记着别人的事」。
       *
       * 同时清掉会话指针：否则在新历史到达前发消息，会带着旧角色的
       * session_id 去请求（后端按归属校验会 404）。
       */
      setMessages([]);
      setSessionId(null);
      setPersonaId(id); // 恢复 effect 会按新角色装载会话
      rememberPreference({ persona_id: id });
    },
    [abortCurrentTurn, resetTurnState, rememberPreference],
  );

  // send 的 catch 要用它做「角色失效」自愈（见上面的 ref 说明）
  changePersonaRef.current = changePersona;

  /** 已校验过的人设 id：同一个 id 只查一次清单，避免每次渲染都打后端 */
  const reconciledRef = useRef("");

  /**
   * 保证「当前陪伴对象」在后端确实存在。
   *
   * 失效的来路：在创作工坊里删了它、在控制台（另一个 origin）删了它、
   * 本地会话指针来自另一份数据目录。不校正的表现就是用户报的那句
   * 「进行对话报 404 找不到人设」——界面还显示着角色名，可每一轮请求都被
   * 后端按记忆命名空间拦下（`未知人设`），而界面上没有任何地方能看出问题。
   *
   * 校正走 `changePersona`：它会清空当前会话并写回偏好，与用户手动切换同一套语义
   * （记忆换了库这件事必须让用户看见），并给一条可读提示。
   */
  useEffect(() => {
    if (!personaId || reconciledRef.current === personaId) return;
    reconciledRef.current = personaId;

    let active = true;
    void (async () => {
      let catalog: PersonaCatalog | null = null;
      try {
        catalog = await getPersonas();
      } catch {
        return; // 清单取不到就**不校正**：宁可维持现状，也不能凭猜测换掉用户的角色
      }
      if (!active || !catalog) return;
      if ((catalog.personas ?? []).some((item) => item.id === personaId)) return;

      const fallback = pickFallbackPersona(catalog);
      if (!fallback.id) return; // 清单为空：报错也换不了，保持原样
      changePersonaRef.current(fallback.id);
      setError(`原来的角色已不存在，已切换到「${fallback.name}」，请重新发送。`);
    })();

    return () => {
      active = false;
    };
  }, [personaId]);

  /**
   * 切换交互模式（桌宠对话 / 酒馆聊天）。
   *
   * **会开一段新对话**：模式是会话级属性（`AGENTS.md §8.1`），后端把它落在
   * `session.mode` 上；在同一会话里逐轮改模式会让上下文语义漂移——前半段按桌宠
   * 隔离记忆、后半段突然把酒馆世界书塞进来。所以这里与「切换陪伴对象」同一套做法：
   * 清掉当前会话指针与界面，历史仍留在「历史记录」里可找回。
   */
  const changeMode = useCallback(
    (next: string) => {
      /*
       * 已经是这个模式就什么都不做。
       *
       * 拿 `mode || effectiveMode` 比而不是只比 `mode`：没选过时后端会按酒馆预设
       * 推断，此时 `mode` 是空串而对话实际跑在酒馆模式——用户点「酒馆聊天」只是想
       * 确认，为这个清掉当前对话是不可接受的。
       */
      if (next === (mode || effectiveMode)) return;

      setModeValue(next);
      abortCurrentTurn();
      resetTurnState();
      setMessages([]);
      setSessionId(null);
      rememberPreference({ mode: next });
    },
    [mode, effectiveMode, abortCurrentTurn, resetTurnState, rememberPreference],
  );

  const changeStyle = useCallback(
    (id: string) => {
      setStyleId(id);
      rememberPreference({ style_id: id });
    },
    [rememberPreference],
  );

  /**
   * 切换叙事框架层。
   *
   * 与 `changeStyle` 同一套做法（改本地 + 写共享偏好），**不切会话、不动记忆**：
   * 它只改写下一轮的提示词框架。写偏好时把 `"none"` 原样存下去——
   * 那正是「用户明确关掉」这个表态本身，抹掉它就等于让部署默认重新说了算。
   */
  const changeJailbreak = useCallback(
    (id: string) => {
      setJailbreakId(id);
      rememberPreference({ jailbreak_id: id });
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
   * 改用户称呼。
   *
   * 与 persona / style 的关键区别：它只影响提示词里的 `{{user_name}}`，
   * **不切会话、不动记忆**（会话按角色隔离，与「我怎么被称呼」无关）。
   *
   * 刻意 await 并把错误抛给调用方（而不是像 `rememberPreference` 那样静默失败）：
   * 输入框要能告诉用户「没保存上」，不能显示一句乐观的「已保存」。
   * 回填用后端返回值，保证本地显示的就是后端存下的那个。
   */
  const changeUserName = useCallback(async (name: string) => {
    const next = await setChatPreferences({ user_name: normalizeUserName(name) });
    setUserName(next.user_name || DEFAULT_USER_NAME);
  }, []);

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

  /**
   * 清空**当前陪伴对象**的全部会话（不可恢复）。
   *
   * 与删单条的区别：这里必然包含当前会话，所以直接回到干净的新对话，
   * 不必逐个判断「删的是不是当前会话」。记忆不受影响（记忆跨会话累积）。
   */
  const clearAllSessions = useCallback(async () => {
    await deleteSessions(personaIdRef.current);
    clearActiveSession();
    setSessionVersion((version) => version + 1);
  }, [clearActiveSession]);

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
    mode,
    setMode: changeMode,
    effectiveMode,
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
    clearAllSessions,
    retryHistory,
    styleId,
    setStyleId: changeStyle,
    jailbreakId,
    setJailbreakId: changeJailbreak,
    presetId,
    setPresetId: changePreset,
    stPresetId,
    setStPresetId: changeStPreset,
    userName,
    setUserName: changeUserName,
    hasStoredPreference,
    stPresetMeta,
    streamingSpeech,
    setStreamingSpeech,
    busy,
    error,
    send,
    interrupt,
    eventChannel,
    proactiveNotice,
    perceptionHint,
    proactiveCount,
  };
}
