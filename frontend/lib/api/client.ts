/** 后端接口封装（与 backend/app/api/*.py 一一对应）。 */

import type {
  AvatarCredentialsResponse,
  AvatarCredentialForm,
  AvatarLayout,
  AvatarModelInfo,
  AvatarModelKind,
  AvatarModelSources,
  AiDraftResponse,
  AvatarTimeline,
  ChatResponse,
  HealthStatus,
  JailbreakCatalog,
  KnowledgeDeleteResult,
  KnowledgeDoc,
  KnowledgeUploadResult,
  LlmConfigInfo,
  LlmConfigInput,
  LlmConfigResponse,
  LlmTestResult,
  PersonaAiDraft,
  PersonaCatalog,
  PluginImportInput,
  PluginImportResponse,
  PluginListResponse,
  PluginReloadResponse,
  PluginSettingsResponse,
  PluginStatus,
  PresetCatalog,
  SessionDeleteResult,
  SessionPurgeResult,
  MemoryPurgeResult,
  SessionHistory,
  SessionSummary,
  SkillAiDraft,
  SkillCatalog,
  SkillCreateInput,
  SkillDeleteResult,
  SkillDetail,
  SkillInfo,
  SkillRestoreResult,
  SkillWriteResponse,
  SpeakCommand,
  StAdaptResult,
  StPresetCatalog,
  StPresetDetail,
  StPresetPatch,
  StPresetResponse,
  StudioCatalog,
  StudioEntryDeleteResult,
  StudioEntryInput,
  StudioEntryTestInput,
  StudioEntryTestResult,
  StudioEntryWriteResult,
  StudioPersona,
  StudioPersonaDeleteResult,
  StudioPersonaInput,
  StudioPersonaWriteResult,
  StudioStyle,
  StudioStyleDeleteResult,
  StudioStyleInput,
  StudioStyleWriteResult,
  StudioWorldBookEntry,
  StyleCatalog,
  TavernBridgeStatus,
  TavernImportResult,
  TavernKnowledgeSyncResult,
  TtsVoicesStatus,
} from "@/lib/api/types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

/**
 * 把后端返回的**相对**媒体地址补全为可直接加载的地址。
 *
 * 后端返回的是 `/media/audio/xxx.wav` 这类相对路径（它不知道自己对外暴露的
 * 域名与端口）；直接交给 `new Audio()` / `<img src>` 会按**前端**源解析，
 * 于是请求打到 localhost:3000 而 404——前后端不同源时必须补全。
 */
export function resolveMediaUrl(path: string): string {
  const value = (path ?? "").trim();
  if (!value) return "";
  if (/^https?:\/\//i.test(value)) return value;
  return `${API_BASE}${value.startsWith("/") ? "" : "/"}${value}`;
}

/**
 * 带 HTTP 状态码的接口错误。
 *
 * 知识库上传的 409（判重命中）需要读出 detail 里的判重详情，交给界面渲染成
 * 「是否覆盖」的确认流程，因此不能像原实现那样只留一句拼接文本。
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    /** 后端 `detail` 原文（字符串或结构体） */
    readonly detail: unknown,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** 读取后端错误体（FastAPI 统一为 `{detail: ...}`）。 */
async function readErrorDetail(res: Response): Promise<unknown> {
  try {
    const body = (await res.json()) as { detail?: unknown } | null;
    return body?.detail ?? null;
  } catch {
    return null;
  }
}

/** detail 转可读文本（字符串直接用，结构体退化为 JSON 片段）。 */
function detailToText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (detail == null) return "";
  try {
    return JSON.stringify(detail).slice(0, 200);
  } catch {
    return "";
  }
}

/** 统一抛出 ApiError（所有非 2xx 响应的唯一出口）。 */
async function throwApiError(res: Response, action: string): Promise<never> {
  const detail = await readErrorDetail(res);
  const text = detailToText(detail);
  throw new ApiError(
    res.status,
    detail,
    `${action}失败（${res.status}）${text ? `：${text}` : ""}`,
  );
}

/**
 * 空对象视为「无」。
 * 后端对「无情绪 / 无播报指令」返回 `{}` 而非 null；若不规范化，调用方会拿到
 * 一个「既非 null、字段又全缺」的伪对象（MoodIndicator 会渲染出 NaN%）。
 */
function emptyToNull<T extends object>(value: T | null | undefined): T | null {
  if (!value || typeof value !== "object") return null;
  return Object.keys(value).length > 0 ? value : null;
}

export interface ChatRequest {
  text: string;
  session_id?: string | null;
  persona_id?: string;
  user_name?: string;
  current_mood?: string | null;
  style_id?: string | null;
  /** 模型预设 id；不传或传 null 时由后端按模型名自动匹配 */
  preset_id?: string | null;
  /**
   * 已导入的 SillyTavern 预设 id；指定后本轮按该预设组装提示词。
   * 与内置 preset_id 相互独立（组装走 ST 预设，采样三分层合并）。
   */
  st_preset_id?: string | null;
  /**
   * 交互模式：`companion`（桌宠对话）/ `tavern`（酒馆聊天）。
   * 不传时由后端按 ST 预设推断；见 `lib/chat/mode.ts`。
   */
  mode?: string | null;
  /**
   * 叙事框架（jailbreak）预设 id，三种取值语义不同：
   * - 不传 / `null`：跟随部署默认（出厂为关闭）
   * - `"none"`：本轮**显式关闭**（覆盖部署默认）
   * - 具体 id：本轮启用该档
   *
   * 前端始终显式传当前选择，因此「用户关掉」不会被动到部署默认覆盖。
   */
  jailbreak_id?: string | null;
}

/** 调用后端对话接口（对话 + 情绪 + 播报指令 + 工具调用）。 */
export async function postChat(
  body: ChatRequest,
  options: { signal?: AbortSignal } = {},
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "对话请求");
  const data = (await res.json()) as ChatResponse;
  return {
    ...data,
    emotion: emptyToNull(data.emotion),
    speak: emptyToNull(data.speak),
  };
}

/** 模型预设档清单（界面「模型预设」选择器用）。 */
export async function getPresets(): Promise<PresetCatalog | null> {
  try {
    const res = await fetch(`${API_BASE}/chat/presets`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as PresetCatalog;
  } catch {
    return null;
  }
}

/**
 * 健康检查（用于展示「后端在线」状态 + MCP 行动层连接情况）。
 *
 * 不抛错：页面用它做轮询探测，离线属预期状态，由调用方按 null 处理。
 */
export async function getHealth(): Promise<HealthStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as Partial<HealthStatus>;
    return {
      status: data.status ?? "",
      app: data.app ?? "",
      // 后端未配置 MCP 清单时该字段可能缺省，统一兜底为空数组
      mcp: Array.isArray(data.mcp) ? data.mcp : [],
      // 插件体系（AGENTS.md §9）：旧后端不返回该字段时兜底为空，前端自会隐藏面板
      plugins: Array.isArray(data.plugins) ? data.plugins : [],
      plugins_summary: data.plugins_summary ?? {
        total: 0,
        started: 0,
        failed: 0,
        by_layer: {},
      },
      // 技能体系（AGENTS.md §9.6）：旧后端不返回该字段时兜底为空，前端自会隐藏面板
      skills: Array.isArray(data.skills) ? data.skills : [],
    };
  } catch {
    return null;
  }
}

// =============================================================
// 个人记忆 / 知识库（POST /knowledge/upload、GET /knowledge/list、
//                  DELETE /knowledge/{doc_id}）
// =============================================================

export interface KnowledgeUploadInput {
  /** 陪伴对象 id（记忆隔离命名空间），与对话的 persona_id 保持一致 */
  companionId: string;
  /** 上传文件；与 text 二选一 */
  file?: File;
  /** 直接粘贴的纯文本；与 file 二选一 */
  text?: string;
  /** 自定义标题（缺省由后端取文件名） */
  title?: string;
  /** 判重命中时是否强制覆盖已有文档（默认 false → 后端返回 409） */
  force?: boolean;
}

/**
 * 上传一篇文档到个人记忆（解析 → 清洗 → 判重 → 分块 → 入库）。
 *
 * 判重命中且未传 force 时抛出 `ApiError`（status 409，detail 为
 * `KnowledgeDuplicateDetail`），由界面弹出「覆盖确认」。
 */
export async function uploadKnowledge(
  input: KnowledgeUploadInput,
  options: { signal?: AbortSignal } = {},
): Promise<KnowledgeUploadResult> {
  const query = new URLSearchParams({ companion_id: input.companionId });
  const form = new FormData();
  if (input.file) form.append("file", input.file);
  if (input.text) form.append("text", input.text);
  if (input.title) form.append("title", input.title);
  form.append("force", input.force ? "true" : "false");

  const res = await fetch(`${API_BASE}/knowledge/upload?${query.toString()}`, {
    method: "POST",
    body: form, // 不设 Content-Type：浏览器需自行补 multipart 边界
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "上传文档");
  return (await res.json()) as KnowledgeUploadResult;
}

/** 列出该陪伴对象已入库的全部文档。失败时抛 ApiError（列表失败必须让用户看见）。 */
export async function listKnowledge(
  companionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<KnowledgeDoc[]> {
  const query = new URLSearchParams({ companion_id: companionId });
  const res = await fetch(`${API_BASE}/knowledge/list?${query.toString()}`, {
    cache: "no-store",
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "读取个人记忆列表");
  return (await res.json()) as KnowledgeDoc[];
}

/** 删除整篇文档及其全部分块。 */
export async function deleteKnowledge(
  docId: string,
  companionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<KnowledgeDeleteResult> {
  const query = new URLSearchParams({ companion_id: companionId });
  const res = await fetch(
    `${API_BASE}/knowledge/${encodeURIComponent(docId)}?${query.toString()}`,
    { method: "DELETE", signal: options.signal },
  );
  if (!res.ok) await throwApiError(res, "删除文档");
  return (await res.json()) as KnowledgeDeleteResult;
}

// =============================================================
// 陪伴对象 / 文风清单（GET /chat/personas、GET /chat/styles）
// =============================================================

/**
 * 人设（陪伴对象）清单。
 *
 * 人设 id 同时是记忆隔离命名空间（companion_id），页面用它渲染选择器、
 * 显示当前角色名，并作为个人记忆面板的命名空间。
 */
/**
 * 对话偏好（人设 / 文风 / 提示词预设 / 酒馆预设 / 叙事框架）。
 *
 * 空串 = 未设置（界面回落到部署声明的默认）。存在**后端**而不是 localStorage：
 * 三个界面（控制台、Web 端、桌宠窗）的 origin 不同，localStorage 天然不共享。
 */
export interface ChatPreferences {
  persona_id: string;
  style_id: string;
  preset_id: string;
  st_preset_id: string;
  /**
   * 叙事框架（jailbreak）选择。**空串与 `"none"` 语义不同**：
   * - 空串 = 没选过 → 界面回落到部署默认（出厂关闭）
   * - `"none"` = 用户**明确关掉** → 即使部署默认开启也不启用
   *
   * 因此界面回填时不能用 `if (prefs.jailbreak_id)` 判空——那会把「明确关掉」
   * 当成「没选过」，部署默认一开就自动亮起来。
   */
  jailbreak_id: string;
  /** 交互模式（`companion` / `tavern`）；空串 = 没选过，由后端按 ST 预设推断 */
  mode: string;
  /** 用户自己的称呼（`{{user_name}}`）；空串 = 没设置过，界面回落到「朋友」 */
  user_name: string;
}

/**
 * 读对话偏好。
 *
 * 返回 `null` 专指「后端读不到」（离线 / 5xx）——与「全都没设置」（空串）是两件事：
 * 前者要保持界面现有选择，后者才回落到部署默认。
 */
export async function getChatPreferences(): Promise<ChatPreferences | null> {
  try {
    const res = await fetch(`${API_BASE}/chat/preferences`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as ChatPreferences;
  } catch {
    return null;
  }
}

/** 改对话偏好（只传要改的字段；空串 = 清除该项） */
export async function setChatPreferences(
  patch: Partial<ChatPreferences>,
): Promise<ChatPreferences> {
  const res = await fetch(`${API_BASE}/chat/preferences`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) await throwApiError(res, "保存对话偏好");
  return (await res.json()) as ChatPreferences;
}

export async function getPersonas(): Promise<PersonaCatalog | null> {
  try {
    const res = await fetch(`${API_BASE}/chat/personas`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as PersonaCatalog;
  } catch {
    return null;
  }
}

/** 文风预设清单（预设与代码分离，新增 YAML 无需改前端）。 */
export async function getStyles(): Promise<StyleCatalog | null> {
  try {
    const res = await fetch(`${API_BASE}/chat/styles`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as StyleCatalog;
  } catch {
    return null;
  }
}

/**
 * 叙事框架（jailbreak）预设清单 + 部署默认开关。
 *
 * 清单里的 `enabled` 是**部署默认**，不是用户选择——用户自己的开关在
 * `GET /chat/preferences` 的 `jailbreak_id` 里。两者都要，因为界面需要区分
 * 「这层在部署层面是否可用」与「我此刻开没开」。
 *
 * 与 `getStyles` 一样返回 `null` 表示后端读不到（离线 / 5xx），此时界面
 * 不渲染这个开关——留着它只会让用户点了没反应。
 */
export async function getJailbreakPresets(): Promise<JailbreakCatalog | null> {
  try {
    const res = await fetch(`${API_BASE}/chat/jailbreak-presets`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as JailbreakCatalog;
  } catch {
    return null;
  }
}

// =============================================================
// 会话历史（GET /chat/sessions、GET /chat/sessions/{id}/history）
// =============================================================

/** 会话列表（按最近更新倒序）；personaId 省略时返回全部陪伴对象的会话。 */
export async function getSessions(
  personaId?: string,
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<SessionSummary[]> {
  const query = new URLSearchParams();
  if (personaId) query.set("persona_id", personaId);
  if (options.limit) query.set("limit", String(options.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const res = await fetch(`${API_BASE}/chat/sessions${suffix}`, {
    cache: "no-store",
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "读取历史会话列表");
  return (await res.json()) as SessionSummary[];
}

/**
 * 取一次会话的历史消息（刷新页面后恢复界面 + 续聊）。
 *
 * 返回的是原始留档；热层窗口（喂给模型的条数）由后端控制，前端不必关心。
 */
export async function getSessionHistory(
  sessionId: string,
  options: { signal?: AbortSignal } = {},
): Promise<SessionHistory> {
  const res = await fetch(
    `${API_BASE}/chat/sessions/${encodeURIComponent(sessionId)}/history`,
    { cache: "no-store", signal: options.signal },
  );
  if (!res.ok) await throwApiError(res, "读取历史消息");
  return (await res.json()) as SessionHistory;
}

/**
 * 删除一段对话及其全部消息（**不可恢复**）。
 *
 * `personaId` 必填：后端会校验会话归属，避免误删其它陪伴对象的对话。
 */
export async function deleteSession(
  sessionId: string,
  personaId: string,
  options: { signal?: AbortSignal } = {},
): Promise<SessionDeleteResult> {
  const query = new URLSearchParams({ persona_id: personaId });
  const res = await fetch(
    `${API_BASE}/chat/sessions/${encodeURIComponent(sessionId)}?${query.toString()}`,
    { method: "DELETE", signal: options.signal },
  );
  if (!res.ok) await throwApiError(res, "删除对话");
  return (await res.json()) as SessionDeleteResult;
}

/**
 * 清空某个陪伴对象的**全部**会话（不可恢复）。
 *
 * 用在「试聊留下一堆同名会话」之后：逐条删不现实，按角色一次清完。
 * 记忆不受影响（记忆按陪伴对象跨会话累积）。
 */
export async function deleteSessions(
  personaId: string,
  options: { signal?: AbortSignal } = {},
): Promise<SessionPurgeResult> {
  const query = new URLSearchParams({ persona_id: personaId });
  const res = await fetch(`${API_BASE}/chat/sessions?${query.toString()}`, {
    method: "DELETE",
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "清空历史对话");
  return (await res.json()) as SessionPurgeResult;
}

/**
 * 清空某个陪伴对象的**全部记忆**（不可恢复）。
 *
 * 四层一起清：情景记忆（温层）/ 语义事实（冷层）/ 情绪日记 / 个人记忆（知识库）。
 * **不动会话记录**——历史对话是另一个入口（`deleteSessions`），
 * 「只清记忆、留着聊天记录」是用户会真的要的组合。
 */
export async function purgeMemory(
  personaId: string,
  options: { signal?: AbortSignal } = {},
): Promise<MemoryPurgeResult> {
  const query = new URLSearchParams({ persona_id: personaId });
  const res = await fetch(`${API_BASE}/chat/memory?${query.toString()}`, {
    method: "DELETE",
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "清空记忆");
  return (await res.json()) as MemoryPurgeResult;
}

// =============================================================
// 播报指令（POST /media/speak）
// =============================================================

export interface SpeakRequest {
  text: string;
  /** 情绪标签（英文，如 anxious） */
  emotion?: string | null;
  /** 情绪强度 0-1 */
  intensity?: number;
  /** 魔珐音色 ID（缺省用服务端默认） */
  voice?: string | null;
  /** 是否返回流式分段（配合 SDK 逐段播报） */
  streaming?: boolean;
  /** 流式分段的字数上限（后端限制 10-200） */
  maxChars?: number;
}

/**
 * 只生成播报指令（不经过对话链路）。
 *
 * 用途：对话播报之外的独立播报（如呼吸引导倒计时），以及
 * `streaming=true` 时取回 `chunks[]` 做分段播报。
 */
export async function postSpeak(
  body: SpeakRequest,
  options: { signal?: AbortSignal } = {},
): Promise<SpeakCommand> {
  const res = await fetch(`${API_BASE}/media/speak`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: body.text,
      emotion: body.emotion ?? null,
      intensity: body.intensity ?? 0.5,
      voice: body.voice ?? null,
      streaming: body.streaming ?? false,
      max_chars: body.maxChars ?? 40,
    }),
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "生成播报指令");
  return (await res.json()) as SpeakCommand;
}

// =============================================================
// 口型 / 表情 / 动作时间轴（POST /media/avatar）
// =============================================================

export interface AvatarTimelineRequest {
  text: string;
  /** 情绪标签（英文，如 anxious） */
  emotion?: string | null;
  /** 情绪强度 0-1 */
  intensity?: number;
  voice?: string | null;
}

/**
 * 取渲染无关的驱动时间轴（口型 / 表情 / 动作 + 可选音频）。
 *
 * 失败一律返回 `null`：口型只是锦上添花，**绝不能让播报链路因为时间轴拿不到而失败**
 * （调用方拿到 null 就是“嘴不动”，其余一切照常）。
 */
export async function postAvatar(
  body: AvatarTimelineRequest,
  options: { signal?: AbortSignal } = {},
): Promise<AvatarTimeline | null> {
  try {
    const res = await fetch(`${API_BASE}/media/avatar`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: body.text,
        emotion: body.emotion ?? null,
        intensity: body.intensity ?? 0.5,
        voice: body.voice ?? null,
      }),
      signal: options.signal,
    });
    if (!res.ok) return null;
    return (await res.json()) as AvatarTimeline;
  } catch {
    // 含 AbortError（切换播报段时主动取消）——同样静默
    return null;
  }
}

// =============================================================
// 语音引擎与音色（GET /media/tts/voices）
// =============================================================

/**
 * 查询当前语音引擎与可选音色。
 *
 * 失败/异常一律返回 `null`：这只是「用不用服务端声音」的探测，
 * 拿不到就按「未接入 TTS」处理（静默），**不该阻断页面加载**（与 postAvatar 同样的处理）。
 */
export async function fetchTtsVoices(
  options: { signal?: AbortSignal } = {},
): Promise<TtsVoicesStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/media/tts/voices`, {
      cache: "no-store",
      signal: options.signal,
    });
    if (!res.ok) return null;
    return (await res.json()) as TtsVoicesStatus;
  } catch {
    return null;
  }
}

// =============================================================
// 数字人模型库（/media/avatar/models/*）
// =============================================================

/** 模型清单（后端不可用时返回空数组：界面退回到“只用内置模型”） */
export async function listAvatarModels(): Promise<AvatarModelInfo[]> {
  try {
    const res = await fetch(`${API_BASE}/media/avatar/models`, { cache: "no-store" });
    if (!res.ok) return [];
    const data = (await res.json()) as { models?: AvatarModelInfo[] };
    return data.models ?? [];
  } catch {
    return [];
  }
}

/**
 * 上传模型。
 *
 * `live2d` 传单个 zip（或整个模型目录打包）；`images` 传若干张图片。
 * 失败时**抛出**带后端 detail 的错误：上传是用户主动发起的操作，
 * 原因（格式不对 / 包里没 model3.json / 路径越界）必须让用户看到。
 */
export async function uploadAvatarModel(input: {
  kind: AvatarModelKind;
  name: string;
  files: File[];
}): Promise<AvatarModelInfo> {
  const form = new FormData();
  form.append("kind", input.kind);
  form.append("name", input.name);
  for (const file of input.files) form.append("files", file);

  const res = await fetch(`${API_BASE}/media/avatar/models`, { method: "POST", body: form });
  if (!res.ok) await throwApiError(res, "上传模型");
  return (await res.json()) as AvatarModelInfo;
}

/**
 * 更新模型（改名 / 指定情绪映射 / 构图校准）。
 *
 * `layout` **不传** = 保持原校准；传 `null` = 清除校准（回到默认构图）。
 * 后端按「请求里有没有这个键」区分这两件事，所以这里不能把它写成 `layout: null` 的默认值。
 */
export async function updateAvatarModel(
  modelId: string,
  patch: {
    name?: string;
    expressionMap?: Record<string, string>;
    layout?: AvatarLayout | null;
  },
): Promise<AvatarModelInfo> {
  const res = await fetch(`${API_BASE}/media/avatar/models/${encodeURIComponent(modelId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) await throwApiError(res, "更新模型");
  return (await res.json()) as AvatarModelInfo;
}

/**
 * 当前选用的模型 id（空串 = 内置模型）。
 *
 * 返回 `null` 专指「后端读不到」（离线 / 5xx）——与「没选任何模型」（空串）是两件事：
 * 前者要保留本地缓存，后者要回落到内置模型。
 */
export async function getAvatarSelection(): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/media/avatar/models/selection`, { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as { modelId?: string };
    return String(data.modelId ?? "");
  } catch {
    return null;
  }
}

/** 切换当前选用的模型（空串 = 回到内置模型）；返回后端归一后的值 */
export async function setAvatarSelection(modelId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/media/avatar/models/selection`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ modelId }),
  });
  if (!res.ok) await throwApiError(res, "切换当前模型");
  const data = (await res.json()) as { modelId?: string };
  return String(data.modelId ?? "");
}

/** 删除模型（连同其全部文件） */
export async function deleteAvatarModel(modelId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/media/avatar/models/${encodeURIComponent(modelId)}`, {
    method: "DELETE",
  });
  if (!res.ok) await throwApiError(res, "删除模型");
}

/**
 * 模型内文件的 URL（给渲染器用）。
 *
 * 逐段编码：模型里带中文名（`开心兴奋.exp3.json`）与子目录，
 * 直接拼接会把中文/斜杠/空格弄坏，导致表情文件全部 404。
 */
export function avatarModelFileUrl(modelId: string, filePath: string): string {
  const encoded = filePath
    .split("/")
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
  return `${API_BASE}/media/avatar/models/${encodeURIComponent(modelId)}/files/${encoded}`;
}

/**
 * 模型来源：本机库（已装）+ 清单（可获取）。
 *
 * 与 `listAvatarModels()` 的分工：那个只列**已装**的（可直接选用），
 * 这个还回答「还有哪些可以获取」，并带作者与授权信息。
 *
 * 清单里的条目**没有本地文件**，本项目也不代下载（模型不可自由分发，
 * 见 `docs/license-compliance.md`）——界面只展示「去哪拿」。
 */
export async function listAvatarModelSources(): Promise<AvatarModelSources> {
  const res = await fetch(`${API_BASE}/media/avatar/models/sources`, { cache: "no-store" });
  if (!res.ok) await throwApiError(res, "读取模型来源");
  return (await res.json()) as AvatarModelSources;
}

// =============================================================
// 数字人凭证（/media/avatar/credentials）
// =============================================================

/**
 * 读某形态的魔珐凭证。
 *
 * 返回 `null` 专指「后端读不到」（离线 / 5xx）——与「后端说没配」
 * （`source: "none"`）是两件事：前者要保持界面现状，后者才是
 * 「未配置 → 用本地渲染器」。
 */
export async function getAvatarCredentials(
  form: AvatarCredentialForm,
): Promise<AvatarCredentialsResponse | null> {
  try {
    const res = await fetch(
      `${API_BASE}/media/avatar/credentials?form=${encodeURIComponent(form)}`,
      { cache: "no-store" },
    );
    if (!res.ok) return null;
    return (await res.json()) as AvatarCredentialsResponse;
  } catch {
    return null;
  }
}

/**
 * 写某形态的凭证（两个字段都传空串 = 清除）。
 *
 * 失败返回 `null` 而不抛错：调用方是设置面板，它只需要知道「成没成」，
 * 由界面给出可读提示（与 `getChatPreferences` 同一套约定）。
 */
export async function putAvatarCredentials(input: {
  form: AvatarCredentialForm;
  appId: string;
  appSecret: string;
}): Promise<AvatarCredentialsResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/media/avatar/credentials`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
    if (!res.ok) return null;
    return (await res.json()) as AvatarCredentialsResponse;
  } catch {
    return null;
  }
}

// =============================================================
// SillyTavern 预设（导入 / 编辑 / 导出，契约见 docs/st-preset-compat.md）
// =============================================================

/**
 * 已导入预设清单。
 *
 * 与 `getPresets()` 的区别：那份是**内置**采样档（随代码发布），
 * 这份是用户自己导入的酒馆预设（存本地目录，不进仓库）。
 * 失败时返回 null（后端离线不该挡住界面）。
 */
export async function listStPresets(): Promise<StPresetCatalog | null> {
  try {
    const res = await fetch(`${API_BASE}/chat/st-presets`, { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as Partial<StPresetCatalog>;
    return { dir: data.dir ?? "", presets: data.presets ?? [] };
  } catch {
    return null;
  }
}

export interface StPresetImportInput {
  /** 预设 JSON 原文（用户本地文件内容） */
  content: string;
  /** 原始文件名（用于推导预设 id） */
  sourceFile?: string;
  /** 允许覆盖同名预设（会作废其界面编辑） */
  overwrite?: boolean;
}

/**
 * 导入一份 ST 预设。
 *
 * 解析失败返回 400（后端不落盘），id 已存在且未传 overwrite 时同样报错 ——
 * 界面据此提示用户是否覆盖，而不是默默改成一个新 id。
 */
export async function importStPreset(
  input: StPresetImportInput,
  options: { signal?: AbortSignal } = {},
): Promise<StPresetResponse> {
  const res = await fetch(`${API_BASE}/chat/st-presets/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content: input.content,
      source_file: input.sourceFile ?? "",
      overwrite: input.overwrite ?? false,
    }),
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "导入预设");
  return (await res.json()) as StPresetResponse;
}

/** 预设详情（归一化结构 + 条目清单 + 未生效特性）。 */
export async function getStPreset(
  presetId: string,
  options: { signal?: AbortSignal } = {},
): Promise<StPresetResponse> {
  const res = await fetch(`${API_BASE}/chat/st-presets/${encodeURIComponent(presetId)}`, {
    cache: "no-store",
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "读取预设详情");
  return (await res.json()) as StPresetResponse;
}

/**
 * 写入覆盖层（开关 / 顺序 / depth / role / 正文 / 记忆注入）。
 * 原始导入文件始终只读，所以「重置」永远可用。
 */
export async function patchStPreset(
  presetId: string,
  patch: StPresetPatch,
  options: { signal?: AbortSignal } = {},
): Promise<StPresetDetail> {
  const res = await fetch(`${API_BASE}/chat/st-presets/${encodeURIComponent(presetId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "保存预设修改");
  const data = (await res.json()) as { detail: StPresetDetail };
  return data.detail;
}

/**
 * AI 适配：把导入的酒馆预设改造成本项目的陪伴对话形态。
 *
 * 默认 `dryRun=true` —— 只返回建议（问题清单 + 覆盖层补丁 + 前后对照），
 * 用户确认后再传 `dryRun=false` 落盘到覆盖层。原始导入文件始终只读。
 */
export async function adaptStPreset(
  presetId: string,
  options: { dryRun?: boolean; useModel?: boolean; signal?: AbortSignal } = {},
): Promise<StAdaptResult> {
  const res = await fetch(
    `${API_BASE}/chat/st-presets/${encodeURIComponent(presetId)}/ai-adapt`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dry_run: options.dryRun ?? true,
        use_model: options.useModel ?? true,
      }),
      signal: options.signal,
    },
  );
  if (!res.ok) await throwApiError(res, "AI 适配预设");
  return (await res.json()) as StAdaptResult;
}

/** 清空覆盖层，回到导入时的状态。 */
export async function resetStPreset(presetId: string): Promise<StPresetDetail> {
  const res = await fetch(
    `${API_BASE}/chat/st-presets/${encodeURIComponent(presetId)}/reset`,
    { method: "POST" },
  );
  if (!res.ok) await throwApiError(res, "重置预设");
  const data = (await res.json()) as { detail: StPresetDetail };
  return data.detail;
}

/** 删除导入的预设及其覆盖层。 */
export async function deleteStPreset(presetId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/st-presets/${encodeURIComponent(presetId)}`, {
    method: "DELETE",
  });
  if (!res.ok) await throwApiError(res, "删除预设");
}

/**
 * 导出为 ST 兼容 JSON（可直接带回酒馆）。
 *
 * 注意：导入时为安全剥离的端点/密钥类字段不会被还原（后端从未保存它们的值）。
 */
export async function exportStPreset(
  presetId: string,
  applyOverride = true,
): Promise<Record<string, unknown>> {
  const query = new URLSearchParams({ apply_override: String(applyOverride) });
  const res = await fetch(
    `${API_BASE}/chat/st-presets/${encodeURIComponent(presetId)}/export?${query.toString()}`,
    { cache: "no-store" },
  );
  if (!res.ok) await throwApiError(res, "导出预设");
  return (await res.json()) as Record<string, unknown>;
}

// =============================================================
// 对话模型的运行时切换（/llm/*）
// =============================================================

/**
 * 当前生效的模型配置。
 *
 * 失败返回 null（后端离线时不该挡住界面）；注意响应里**没有 api_key 明文**，
 * 只有 `has_api_key`——key 只在后端本地文件里，前端只能提交「要改的新值」。
 */
export async function getLlmConfig(): Promise<LlmConfigResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/llm/config`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as LlmConfigResponse;
  } catch {
    return null;
  }
}

/** 切换对话模型（立即生效；默认写入后端本地运行时配置）。 */
export async function updateLlmConfig(input: LlmConfigInput): Promise<LlmConfigInfo> {
  const res = await fetch(`${API_BASE}/llm/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) await throwApiError(res, "切换模型");
  const data = (await res.json()) as { config: LlmConfigInfo };
  return data.config;
}

/** 清除界面设置，回到部署配置（.env）。 */
export async function resetLlmConfig(): Promise<LlmConfigInfo> {
  const res = await fetch(`${API_BASE}/llm/config`, { method: "DELETE" });
  if (!res.ok) await throwApiError(res, "恢复部署配置");
  const data = (await res.json()) as { config: LlmConfigInfo };
  return data.config;
}

/**
 * 拉取端点可用模型（`GET {base_url}/models`）。
 *
 * `apiKey` 不传时后端用已保存的；传了只在本次请求内使用，不落盘。
 */
export async function listLlmModels(params: {
  baseUrl?: string;
  apiKey?: string | null;
  provider?: string;
}): Promise<string[]> {
  const query = new URLSearchParams();
  if (params.baseUrl) query.set("base_url", params.baseUrl);
  if (params.apiKey) query.set("api_key", params.apiKey);
  if (params.provider) query.set("provider", params.provider);
  const res = await fetch(`${API_BASE}/llm/models?${query.toString()}`, { cache: "no-store" });
  if (!res.ok) await throwApiError(res, "拉取模型列表");
  const data = (await res.json()) as { models?: string[] };
  return data.models ?? [];
}

/**
 * 连通性测试：发一条极短请求，返回成功与否与延迟。
 * 不改变当前配置、不落盘——用于「应用」之前先确认三件套是否正确。
 */
export async function testLlmConfig(input: LlmConfigInput): Promise<LlmTestResult> {
  const res = await fetch(`${API_BASE}/llm/config/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) await throwApiError(res, "测试连接");
  return (await res.json()) as LlmTestResult;
}

export { API_BASE };

// =============================================================
// 创作工坊（/chat/studio/*，backend/app/api/studio.py）
// =============================================================

/**
 * 工坊写接口的公共封装。
 *
 * 失败一律抛 `ApiError`（带上后端 detail 原文）——工坊的报错都是「用户能自己改」
 * 的那类（名字为空、正则写坏、内置只读），把原文显示出来比笼统的「保存失败」有用得多。
 */
async function studioJson<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body: unknown,
  action: string,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) await throwApiError(res, action);
  return (await res.json()) as T;
}

/** 工坊的一次性加载：角色清单 + 世界书条目 + 写作提示（变量名 / 字段上限）。 */
export async function getStudioCatalog(
  options: { signal?: AbortSignal } = {},
): Promise<StudioCatalog> {
  const res = await fetch(`${API_BASE}/chat/studio/catalog`, {
    cache: "no-store",
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "读取创作工坊");
  return (await res.json()) as StudioCatalog;
}

/** 角色详情（含人设正文；内置角色也会返回，由界面置为只读）。 */
export async function getStudioPersona(personaId: string): Promise<StudioPersona> {
  const res = await fetch(
    `${API_BASE}/chat/studio/personas/${encodeURIComponent(personaId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) await throwApiError(res, "读取角色");
  return ((await res.json()) as { persona: StudioPersona }).persona;
}

/** 新建角色卡（id 由后端生成，创建后不可改——它是记忆隔离命名空间）。 */
export async function createStudioPersona(
  input: StudioPersonaInput,
): Promise<StudioPersonaWriteResult> {
  return studioJson<StudioPersonaWriteResult>(
    "/chat/studio/personas",
    "POST",
    input,
    "新建角色",
  );
}

/** 更新角色卡（未提交的字段不会被修改）。 */
export async function updateStudioPersona(
  personaId: string,
  input: Partial<StudioPersonaInput>,
): Promise<StudioPersonaWriteResult> {
  return studioJson<StudioPersonaWriteResult>(
    `/chat/studio/personas/${encodeURIComponent(personaId)}`,
    "PUT",
    input,
    "保存角色",
  );
}

/**
 * 删除角色卡（**级联**）。
 *
 * 该角色的全部会话、四层记忆与专属世界书条目都会一并清除，**不可恢复**——
 * 界面必须先做二次确认；响应回报各层实际删除的条数。
 */
export async function deleteStudioPersona(
  personaId: string,
): Promise<StudioPersonaDeleteResult> {
  return studioJson<StudioPersonaDeleteResult>(
    `/chat/studio/personas/${encodeURIComponent(personaId)}`,
    "DELETE",
    undefined,
    "删除角色",
  );
}

/** 复制角色（内置角色借此得到一份可编辑的副本）。 */
export async function duplicateStudioPersona(
  personaId: string,
  name?: string,
): Promise<StudioPersonaWriteResult> {
  return studioJson<StudioPersonaWriteResult>(
    `/chat/studio/personas/${encodeURIComponent(personaId)}/duplicate`,
    "POST",
    name ? { name } : {},
    "复制角色",
  );
}

/** 新建世界书条目。 */
export async function createStudioEntry(
  input: StudioEntryInput,
): Promise<StudioEntryWriteResult> {
  return studioJson<StudioEntryWriteResult>(
    "/chat/studio/worldbook",
    "POST",
    input,
    "新建条目",
  );
}

/** 更新世界书条目（未提交的字段不会被修改）。 */
export async function updateStudioEntry(
  entryId: string,
  input: Partial<StudioEntryInput>,
): Promise<StudioEntryWriteResult> {
  return studioJson<StudioEntryWriteResult>(
    `/chat/studio/worldbook/${encodeURIComponent(entryId)}`,
    "PUT",
    input,
    "保存条目",
  );
}

/** 删除世界书条目（内置条目写入用户侧隐藏清单，不修改包内文件）。 */
export async function deleteStudioEntry(entryId: string): Promise<StudioEntryDeleteResult> {
  return studioJson<StudioEntryDeleteResult>(
    `/chat/studio/worldbook/${encodeURIComponent(entryId)}`,
    "DELETE",
    undefined,
    "删除条目",
  );
}

/**
 * 启用 / 停用条目。
 *
 * 内置条目的开关只写**用户侧的停用偏好**（后端不修改内置 YAML）。
 */
export async function setStudioEntryEnabled(
  entryId: string,
  enabled: boolean,
): Promise<StudioEntryWriteResult> {
  return studioJson<StudioEntryWriteResult>(
    `/chat/studio/worldbook/${encodeURIComponent(entryId)}/enabled`,
    "PATCH",
    { enabled },
    enabled ? "启用条目" : "停用条目",
  );
}

/**
 * 「试触发」：用**草稿**（不落盘）判断一段样例文本是否命中。
 *
 * 草稿字段与保存时的校验规则完全一致（后端共用同一套清洗/校验），
 * 因此不会出现「试的时候命中、存下来却不命中」。
 */
export async function testStudioEntry(
  input: StudioEntryTestInput,
): Promise<StudioEntryTestResult> {
  return studioJson<StudioEntryTestResult>(
    "/chat/studio/worldbook/test",
    "POST",
    input,
    "试触发",
  );
}

// ---------- 文风预设（/chat/studio/styles*）----------
//
// 文风与角色正交，所以这里没有归属也没有级联；但每个写操作同样会让后端
// 失效重建对话图——「工坊里建好、对话里选不到」那种不一致因此不会发生。

/** 文风详情（含风格指令与示例；内置文风也会返回，由界面置为只读）。 */
export async function getStudioStyle(styleId: string): Promise<StudioStyle> {
  const res = await fetch(
    `${API_BASE}/chat/studio/styles/${encodeURIComponent(styleId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) await throwApiError(res, "读取文风");
  return ((await res.json()) as { style: StudioStyle }).style;
}

/** 新建文风预设（id 由后端生成）。 */
export async function createStudioStyle(
  input: StudioStyleInput,
): Promise<StudioStyleWriteResult> {
  return studioJson<StudioStyleWriteResult>(
    "/chat/studio/styles",
    "POST",
    input,
    "新建文风",
  );
}

/** 更新文风预设（未提交的字段不会被修改）。 */
export async function updateStudioStyle(
  styleId: string,
  input: Partial<StudioStyleInput>,
): Promise<StudioStyleWriteResult> {
  return studioJson<StudioStyleWriteResult>(
    `/chat/studio/styles/${encodeURIComponent(styleId)}`,
    "PUT",
    input,
    "保存文风",
  );
}

/** 删除文风预设（内置文风写入用户侧隐藏清单，不修改包内文件）。 */
export async function deleteStudioStyle(
  styleId: string,
): Promise<StudioStyleDeleteResult> {
  return studioJson<StudioStyleDeleteResult>(
    `/chat/studio/styles/${encodeURIComponent(styleId)}`,
    "DELETE",
    undefined,
    "删除文风",
  );
}

/** 复制文风预设（内置文风借此得到一份可编辑的副本）。 */
export async function duplicateStudioStyle(
  styleId: string,
  name?: string,
): Promise<StudioStyleWriteResult> {
  return studioJson<StudioStyleWriteResult>(
    `/chat/studio/styles/${encodeURIComponent(styleId)}/duplicate`,
    "POST",
    name ? { name } : {},
    "复制文风",
  );
}

// =============================================================
// 插件管理（/plugins/*，backend/app/api/plugins.py）
// =============================================================
//
// 数据源分工：**列表**走 `/health`（页面已在轮询，不另开第二条来源）；
// 这里只封装「按需动作」：配置在展开详情时才读，启停在点按钮时才发。
// 启停与保存都返回最新的完整快照，调用方据此就地更新，不必再轮询一次。

/** POST /plugins/{id}/enabled 响应 */
export interface PluginToggleResult {
  id: string;
  enabled: boolean;
  plugins: PluginStatus[];
}

/** PUT /plugins/{id}/settings 响应（与 GET 同形状，前端不必处理两套；同样不含密钥明文） */
export interface PluginSettingsWriteResult {
  id: string;
  values: Record<string, unknown>;
  secrets_set: string[];
  plugins: PluginStatus[];
}

/**
 * 启用 / 禁用插件。
 *
 * 后端会**落盘**（`data/plugins/<id>/state.json`）并在本次运行内立即生效，
 * 因此不必提示用户重启。core 层插件禁用会得到 400。
 */
export async function setPluginEnabled(
  pluginId: string,
  enabled: boolean,
): Promise<PluginToggleResult> {
  const res = await fetch(`${API_BASE}/plugins/${encodeURIComponent(pluginId)}/enabled`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) await throwApiError(res, enabled ? "启用插件" : "禁用插件");
  return (await res.json()) as PluginToggleResult;
}

/** 读插件配置 + 它的 JSON Schema（前端据此渲染表单，插件不自带 UI）。 */
export async function getPluginSettings(pluginId: string): Promise<PluginSettingsResponse> {
  const res = await fetch(`${API_BASE}/plugins/${encodeURIComponent(pluginId)}/settings`, {
    cache: "no-store",
  });
  if (!res.ok) await throwApiError(res, "读取插件配置");
  return (await res.json()) as PluginSettingsResponse;
}

/**
 * 保存插件配置；后端会重新 setup，让新配置当场生效。
 *
 * 密钥字段的约定：**不提交 = 沿用已保存的**（密码框不回填，用户没动它），
 * 提交 `null` = 清除（回落 .env）。因此不要把未修改的密钥字段当成空值提交。
 */
export async function putPluginSettings(
  pluginId: string,
  values: Record<string, unknown>,
): Promise<PluginSettingsWriteResult> {
  const res = await fetch(`${API_BASE}/plugins/${encodeURIComponent(pluginId)}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ values }),
  });
  if (!res.ok) await throwApiError(res, "保存插件配置");
  return (await res.json()) as PluginSettingsWriteResult;
}

/**
 * 酒馆接入状态：能读到什么（条目 / 角色卡 / 会话计数）+ 已导入进度。
 *
 * 插件未启用或未填目录时后端返回 **409**，属预期状态而非异常——
 * 调用方应捕获 `ApiError` 并按 `status === 409` 渲染「请先启用并填目录」，
 * 而不是弹一个错误框。
 */
export async function getTavernBridgeStatus(companionId: string): Promise<TavernBridgeStatus> {
  const query = new URLSearchParams({ companion_id: companionId });
  const res = await fetch(`${API_BASE}/plugins/tavern-bridge/status?${query}`, {
    cache: "no-store",
  });
  if (!res.ok) await throwApiError(res, "读取酒馆接入状态");
  return (await res.json()) as TavernBridgeStatus;
}

/**
 * 把酒馆会话导入为长期记忆。
 *
 * `force` 为 true 时忽略「已导入」记录重新导入（换 embedding 后重建用）——
 * 会重复写入记忆条目，界面上须二次确认。
 */
export async function importTavernMemory(
  companionId: string,
  force = false,
): Promise<TavernImportResult> {
  const query = new URLSearchParams({ companion_id: companionId, force: String(force) });
  const res = await fetch(`${API_BASE}/plugins/tavern-bridge/import?${query}`, {
    method: "POST",
  });
  if (!res.ok) await throwApiError(res, "导入酒馆记忆");
  return (await res.json()) as TavernImportResult;
}

/**
 * 把酒馆世界书同步进**个人知识库**（按角色分作用域，覆盖写）。
 *
 * 与「注入到提示词」是两条路：注入是每轮都可能占预算的**常驻**；
 * 知识库是**按需召回**的索引——问到相关的事才进上下文。
 */
export async function syncTavernKnowledge(): Promise<TavernKnowledgeSyncResult> {
  const res = await fetch(`${API_BASE}/plugins/tavern-bridge/knowledge/sync`, {
    method: "POST",
  });
  if (!res.ok) await throwApiError(res, "同步酒馆世界书到知识库");
  return (await res.json()) as TavernKnowledgeSyncResult;
}

// =============================================================
// 技能（/skills/*，backend/app/api/skills.py，见 AGENTS.md §9.6）
// =============================================================
//
// 清单走 `/health`（页面已在轮询，不另开第二条来源）；这里只封装按需动作：
// 正文在展开某项时才拉，启停与重扫是显式用户操作。

/** POST /skills/{id}/enabled 响应 */
export interface SkillToggleResult {
  id: string;
  enabled: boolean;
  skills: SkillInfo[];
}

/** 技能清单（含启用状态，**不含正文**）。 */
export async function listSkills(): Promise<SkillCatalog> {
  const res = await fetch(`${API_BASE}/skills`, { cache: "no-store" });
  if (!res.ok) await throwApiError(res, "读取技能清单");
  return (await res.json()) as SkillCatalog;
}

/** 技能详情（含 Markdown 正文）——只在展开某项时拉。 */
export async function getSkill(skillId: string): Promise<SkillDetail> {
  const res = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillId)}`, {
    cache: "no-store",
  });
  if (!res.ok) await throwApiError(res, "读取技能详情");
  return (await res.json()) as SkillDetail;
}

/** 启用 / 禁用技能（后端落盘，重启后保持）。 */
export async function setSkillEnabled(
  skillId: string,
  enabled: boolean,
): Promise<SkillToggleResult> {
  const res = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillId)}/enabled`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) await throwApiError(res, enabled ? "启用技能" : "禁用技能");
  return (await res.json()) as SkillToggleResult;
}

/**
 * 重新扫描技能目录。
 *
 * 技能是**文件形态**的（用户可以随时往 `USER_SKILLS_DIR` 丢一个 SKILL.md），
 * 所以给一个免重启的入口——这与 §9.7「插件不做热重载」不矛盾：
 * 那边重载要重跑插件代码，这边只是重读几个 Markdown 文件。
 */
export async function reloadSkills(): Promise<SkillCatalog> {
  const res = await fetch(`${API_BASE}/skills/reload`, { method: "POST" });
  if (!res.ok) await throwApiError(res, "重新扫描技能");
  return (await res.json()) as SkillCatalog;
}

/**
 * 删除技能。
 *
 * 两种语义（由后端按来源定，界面只管如实说明）：
 * - **自建**（`data/skills/`）：删掉磁盘上的文件，删了就没了；
 * - **内置**（`backend/skills/`）：只写用户侧隐藏清单，包内文件一字未改，
 *   清单里仍带着它（`deleted: true`），可以 `restoreSkill` 找回。
 */
export async function deleteSkill(skillId: string): Promise<SkillDeleteResult> {
  const res = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillId)}`, {
    method: "DELETE",
  });
  if (!res.ok) await throwApiError(res, "删除技能");
  return (await res.json()) as SkillDeleteResult;
}

/** 恢复被隐藏的内置技能（自建技能是物理删除，没有可恢复的对象）。 */
export async function restoreSkill(skillId: string): Promise<SkillRestoreResult> {
  const res = await fetch(`${API_BASE}/skills/${encodeURIComponent(skillId)}/restore`, {
    method: "POST",
  });
  if (!res.ok) await throwApiError(res, "恢复技能");
  return (await res.json()) as SkillRestoreResult;
}

// =============================================================
// LLM 辅助创作（AGENTS.md §9.12）：生成草稿 → 用户审阅 → 显式保存
// =============================================================
//
// 三处 `/ai-draft` 都**只返回内容、不落盘**；写操作是各自独立的接口。
// 失败语义统一：400 = 前置条件不满足（如模型是本地占位实现，提示里说清了怎么办），
// 502 = 模型没产出可解析的内容（可以再点一次试试），两者都值得原样展示给用户。

/** 人设草稿（走当前对话模型；不落盘，保存仍由创作工坊的接口负责）。 */
export async function draftPersona(brief: string): Promise<AiDraftResponse<PersonaAiDraft>> {
  const res = await fetch(`${API_BASE}/chat/studio/personas/ai-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brief }),
  });
  if (!res.ok) await throwApiError(res, "生成人设草稿");
  return (await res.json()) as AiDraftResponse<PersonaAiDraft>;
}

/** 技能草稿（不落盘）。 */
export async function draftSkill(brief: string): Promise<AiDraftResponse<SkillAiDraft>> {
  const res = await fetch(`${API_BASE}/skills/ai-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ brief }),
  });
  if (!res.ok) await throwApiError(res, "生成技能草稿");
  return (await res.json()) as AiDraftResponse<SkillAiDraft>;
}

/** 新建技能：写入用户技能目录，并在同一次请求里重扫技能库。 */
export async function createSkill(input: SkillCreateInput): Promise<SkillWriteResponse> {
  const res = await fetch(`${API_BASE}/skills`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) await throwApiError(res, "新建技能");
  return (await res.json()) as SkillWriteResponse;
}

/**
 * 插件完整列表（含扫描目录）。
 *
 * 列表本身页面已从 `/health` 轮询拿到；这里只在需要 `dirs`（插件放哪儿）时按需调用，
 * 因此**不参与轮询**。
 */
export async function getPlugins(): Promise<PluginListResponse> {
  const res = await fetch(`${API_BASE}/plugins`, { cache: "no-store" });
  if (!res.ok) await throwApiError(res, "读取插件列表");
  return (await res.json()) as PluginListResponse;
}

/**
 * 把**写在别处**的插件目录导入到用户插件目录（复制一份）。
 *
 * 导入 = 引入可执行代码：插件跑在宿主进程里，没有沙箱。开发期更推荐把插件所在目录
 * 填进 `PLUGIN_EXTRA_DIRS`（零拷贝，宿主不碰源文件），改完代码点「重新扫描」即可。
 */
export async function importPlugin(
  input: PluginImportInput,
): Promise<PluginImportResponse> {
  const res = await fetch(`${API_BASE}/plugins/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) await throwApiError(res, "导入插件");
  return (await res.json()) as PluginImportResponse;
}

/**
 * 重扫插件目录，发现新放进去的插件（含 `PLUGIN_EXTRA_DIRS` 指向的目录）。
 *
 * 只读 `manifest.json` 并登记，**不重跑**已加载插件的代码——它是「发现」不是「热重载」。
 */
export async function reloadPlugins(): Promise<PluginReloadResponse> {
  const res = await fetch(`${API_BASE}/plugins/reload`, { method: "POST" });
  if (!res.ok) await throwApiError(res, "重新扫描插件目录");
  return (await res.json()) as PluginReloadResponse;
}

// =============================================================
// 感知层（/perception/*，backend/app/api/perception.py）
// 设计见 docs/proactive-multimodal.md §4
// =============================================================

/** 语音转写结果。 */
export interface AsrResult {
  text: string;
  provider: string;
  language: string;
  duration_seconds: number;
  warnings: string[];
  /** 是否识别到内容。为空时**不要覆盖用户已输入的文字** */
  has_text: boolean;
}

/** 桌面情景上报体（Electron 桌宠端采集，Web 端拿不到）。 */
export interface DesktopContextReport {
  now_playing_title?: string;
  now_playing_artist?: string;
  foreground_title?: string;
  foreground_process?: string;
  idle_seconds?: number;
  battery_percent?: number | null;
  battery_charging?: boolean | null;
  /** 客户端本地时间 HH:MM（由客户端给，避免主机与用户时区不一致） */
  local_time?: string;
  /** 客户端本地日期 YYYY-MM-DD */
  local_date?: string;
}

/** 感知能力状态（前端据此决定渲染哪些入口）。 */
export interface PerceptionStatus {
  desktop_enabled: boolean;
  include_window_title: boolean;
  asr_provider: string;
  asr_available: boolean;
  asr_model: string;
  asr_note: string;
  /** 是否在对话界面渲染语音按钮（关掉后能力仍在，只是不显示入口） */
  asr_ui_enabled: boolean;
  /** 语音快捷键（形如 `Ctrl+Shift+M`；空 = 不启用） */
  asr_shortcut: string;
  vision_provider: string;
  vision_available: boolean;
  vision_model: string;
  vision_note: string;
  /** 是否在对话界面渲染「图片」按钮 */
  vision_ui_enabled: boolean;
  /** 是否把时间（日期 / 星期 / 时段）告诉角色 */
  include_time: boolean;
  /** 行踪（活动轨迹）是否在记录 */
  activity_enabled: boolean;
  /** 行踪里是否连窗口标题一起记（默认 false） */
  activity_include_title: boolean;
  /** 行踪保留天数 */
  activity_retention_days: number;
  /** 偏好画像是否注入 */
  profile_enabled: boolean;
  /** 此刻在用什么程序（空 = 没采到） */
  activity_current: string;
  /** 最近几段行踪（给界面核对「它到底记了什么」） */
  activity_lines: string[];
  /** 当前生效的感知事实（已渲染成 `- …` 行） */
  current: string[];
  summary: string;
}

/**
 * 把一段录音转写成文本。
 *
 * **只转写、不发送**：结果回填输入框，由用户确认后再走 `/chat`。
 * ASR 有误识别，自动发送会让「识别错一个字」直接变成「对话跑偏」。
 *
 * 失败抛出 `ApiError`（403/400 的 detail 里带着「该把模型放到哪」这类可照做的信息，
 * 直接展示给用户即可）。
 */
export async function transcribeAudio(
  file: File,
  options: { language?: string; signal?: AbortSignal } = {},
): Promise<AsrResult> {
  const query = new URLSearchParams();
  if (options.language) query.set("language", options.language);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}/perception/asr${suffix}`, {
    method: "POST",
    body: form, // 不设 Content-Type：浏览器需自行补 multipart 边界
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "语音识别");
  return (await res.json()) as AsrResult;
}

/**
 * 上报桌面情景（**只由 Electron 桌宠端调用**）。
 *
 * 主进程只负责采集，经 IPC 交给渲染层后由渲染层直连本接口——
 * 主进程不直连后端、也不接触任何模型密钥（见 `docs/desktop-pet.md` §4）。
 *
 * 后端开关关闭时返回 `accepted: false`（并且不会写入快照），
 * 调用方据此停止采集，而不是继续白上报。
 */
export async function reportDesktopContext(
  context: DesktopContextReport,
  options: { signal?: AbortSignal } = {},
): Promise<{ accepted: boolean; enabled: boolean; perception_text: string }> {
  const res = await fetch(`${API_BASE}/perception/desktop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(context),
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "上报桌面情景");
  return (await res.json()) as { accepted: boolean; enabled: boolean; perception_text: string };
}

/**
 * 感知能力状态。
 *
 * 失败返回 `null`（与 `fetchTtsVoices` 同一约定）：这只是「要不要渲染麦克风」的探测，
 * 拿不到就按「不可用」处理，不该阻断页面加载。
 */
export async function getPerceptionStatus(
  options: { signal?: AbortSignal } = {},
): Promise<PerceptionStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/perception/status`, {
      cache: "no-store",
      signal: options.signal,
    });
    if (!res.ok) return null;
    return (await res.json()) as PerceptionStatus;
  } catch {
    return null;
  }
}

/** 最近的行踪与偏好画像（**给界面看，不是给模型的**）。 */
export interface ActivityReport {
  enabled: boolean;
  include_title: boolean;
  retention_days: number;
  /** 此刻在用什么（空 = 没采到） */
  current: string;
  /** 最近几段，形如 `21:03–21:40 Code.exe` */
  lines: string[];
  /** 偏好画像渲染成的行（常用程序 / 活跃时段 / 夜猫子） */
  profile_lines: string[];
}

/**
 * 读最近的行踪与画像。
 *
 * 「它记住了什么」必须对用户可见——没有这个接口，用户只能靠它说的话去猜，
 * 而猜出来的结论一定比实际更糟。失败返回 `null`（同 `getPerceptionStatus`）。
 */
export async function getActivity(
  options: { hours?: number; signal?: AbortSignal } = {},
): Promise<ActivityReport | null> {
  try {
    const query = new URLSearchParams();
    if (options.hours) query.set("hours", String(options.hours));
    const suffix = query.toString() ? `?${query.toString()}` : "";
    const res = await fetch(`${API_BASE}/perception/activity${suffix}`, {
      cache: "no-store",
      signal: options.signal,
    });
    if (!res.ok) return null;
    return (await res.json()) as ActivityReport;
  } catch {
    return null;
  }
}

/**
 * 清空全部行踪记录。
 *
 * 这是「感知数据用户全权可控」的落地动作：一次清干净，而不是只清今天的——
 * 只清一半反而会让人怀疑「到底删了没」。
 */
export async function clearActivity(
  options: { signal?: AbortSignal } = {},
): Promise<{ removed: number; message: string }> {
  const res = await fetch(`${API_BASE}/perception/activity/clear`, {
    method: "POST",
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "清除行踪");
  return (await res.json()) as { removed: number; message: string };
}

/** 图片理解结果（Qwen2.5-VL）。 */
export interface VisionResult {
  /** 中性事实描述（VLM 产出，已按「只描述看得见的」约束） */
  description: string;
  provider: string;
  model: string;
  warnings: string[];
  /** 后端当前渲染出的「此刻」文本（含这张图），便于展示与排查 */
  perception_text: string;
}

/**
 * 理解一张用户分享的图片，结果作为**感知事实**注入。
 *
 * **只理解、不发送**：描述进后端快照后，仍需用户发一条 `/chat` 才会影响回复。
 * 这样「看图」与「说话」是两次调用、两个模型——视觉模型缺失时，
 * 整个对话链路照常工作。
 *
 * 图片以 data URL 内联进模型请求，**后端不落盘**。
 */
export async function describeImage(
  file: File,
  options: { question?: string; signal?: AbortSignal } = {},
): Promise<VisionResult> {
  const query = new URLSearchParams();
  if (options.question) query.set("question", options.question);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}/perception/vision${suffix}`, {
    method: "POST",
    body: form, // 不设 Content-Type：浏览器需自行补 multipart 边界
    signal: options.signal,
  });
  if (!res.ok) await throwApiError(res, "图片理解");
  return (await res.json()) as VisionResult;
}

// =============================================================
// 主动链路（/proactive/*，backend/app/api/proactive.py）
// 设计见 docs/proactive-multimodal.md §5
// =============================================================
/** 主动链路状态（配置 + 节流状态 + 触发器清单）。 */
export interface ProactiveStatus {
  enabled: boolean;
  running: boolean;
  interval_seconds: number;
  quiet_hours: string;
  min_interval_minutes: number;
  daily_quota: number;
  sent_today: number;
  last_proactive_at: string;
  last_user_message_at: string;
  triggers: { id: string; type: string }[];
  last_outcome: {
    fired: boolean;
    trigger_id: string;
    skipped_reason: string;
    declined: boolean;
  } | null;
}

/** 一次手动评估的结果（演示 / 调试）。 */
export interface ProactiveRunResult {
  fired: boolean;
  trigger_id: string;
  reason: string;
  /** 被闸门拦下的原因（为空表示没有意图，或已成功开口） */
  skipped_reason: string;
  declined: boolean;
  reply: string;
}

/** 主动链路状态（失败返回 null：面板据此隐藏，而不是显示一堆 0）。 */
export async function getProactiveStatus(): Promise<ProactiveStatus | null> {
  try {
    const res = await fetch(`${API_BASE}/proactive/status`, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as ProactiveStatus;
  } catch {
    return null;
  }
}

/**
 * 立刻评估一轮主动开口（演示 / 调试用）。
 *
 * 调主动沟通最难的就是「等它自己到点」——演示视频里不可能等 90 分钟冷却。
 * 它走的是**同一条** `run_once`，因此不会绕过任何一道闸门。
 *
 * ⚠️ 有 SSE 订阅者才会真的生成（没人听时事件会被总线丢弃，生成纯属浪费一次调用）——
 * 用它做演示时**必须先打开对话界面**。
 */
export async function runProactiveNow(): Promise<ProactiveRunResult> {
  const res = await fetch(`${API_BASE}/proactive/run`, { method: "POST" });
  if (!res.ok) await throwApiError(res, "触发一次主动开口");
  return (await res.json()) as ProactiveRunResult;
}

/** 重置主动链路的节流状态（冷却锚点 / 当日计数 / 触发器去重表）。 */
export async function resetProactiveState(): Promise<void> {
  const res = await fetch(`${API_BASE}/proactive/reset`, { method: "POST" });
  if (!res.ok) await throwApiError(res, "重置主动沟通节流");
}
