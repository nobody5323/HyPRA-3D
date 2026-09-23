/** 后端接口封装（与 backend/app/api/*.py 一一对应）。 */

import type {
  AvatarModelInfo,
  AvatarModelKind,
  AvatarTimeline,
  ChatResponse,
  HealthStatus,
  KnowledgeDeleteResult,
  KnowledgeDoc,
  KnowledgeUploadResult,
  LlmConfigInfo,
  LlmConfigInput,
  LlmConfigResponse,
  LlmTestResult,
  PersonaCatalog,
  PresetCatalog,
  SessionDeleteResult,
  SessionHistory,
  SessionSummary,
  SpeakCommand,
  StAdaptResult,
  StPresetCatalog,
  StPresetDetail,
  StPresetPatch,
  StPresetResponse,
  StudioCatalog,
  StudioEntryInput,
  StudioEntryTestInput,
  StudioEntryTestResult,
  StudioEntryWriteResult,
  StudioPersona,
  StudioPersonaDeleteResult,
  StudioPersonaInput,
  StudioPersonaWriteResult,
  StudioWorldBookEntry,
  StyleCatalog,
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
 * 拿不到就按浏览器原生 TTS 走，**不该阻断页面加载**（与 postAvatar 同样的处理）。
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

/** 更新模型（改名 / 指定情绪映射） */
export async function updateAvatarModel(
  modelId: string,
  patch: { name?: string; expressionMap?: Record<string, string> },
): Promise<AvatarModelInfo> {
  const res = await fetch(`${API_BASE}/media/avatar/models/${encodeURIComponent(modelId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) await throwApiError(res, "更新模型");
  return (await res.json()) as AvatarModelInfo;
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
 * 删除角色卡。
 *
 * **只删角色卡**：该角色的会话与记忆都会保留，响应里的 `sessions` / `bound_entries`
 * 是给界面做删除前提示用的影响面信息。
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

/** 删除世界书条目（内置条目会返回 403）。 */
export async function deleteStudioEntry(entryId: string): Promise<StudioEntryWriteResult> {
  return studioJson<StudioEntryWriteResult>(
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
