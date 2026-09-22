/** 后端接口封装（与 backend/app/api/*.py 一一对应）。 */

import type {
  ChatResponse,
  HealthStatus,
  KnowledgeDeleteResult,
  KnowledgeDoc,
  KnowledgeUploadResult,
  PersonaCatalog,
  PresetCatalog,
  SessionDeleteResult,
  SessionHistory,
  SessionSummary,
  SpeakCommand,
  StPresetCatalog,
  StPresetDetail,
  StPresetPatch,
  StPresetResponse,
  StyleCatalog,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

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

export { API_BASE };
