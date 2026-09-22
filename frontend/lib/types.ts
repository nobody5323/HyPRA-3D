/** 与后端接口对齐的类型定义（backend/app/api/*.py）。 */

/** 情绪判定（M4 情绪链路输出） */
export interface EmotionInfo {
  label: string;
  label_zh: string;
  intensity: number;
  confidence: number;
  evidence?: string;
  facial_expression: string;
  source: string;
}

/** 播报指令（M5：交给 SDK 的 speak()） */
export interface SpeakCommand {
  ssml: string;
  display_text: string;
  voice: string;
  emotion: string;
  ka_action: string;
  tone: string;
  intensity: number;
  is_streaming?: boolean;
  /**
   * 流式分段纯文本（`POST /media/speak` 传 streaming=true 时返回）：字幕按段推进。
   */
  chunks?: string[];
  /**
   * 与 chunks 一一对应的 SSML 段：逐段投喂 SDK。
   * 两段之间必须经 interactive_idle 过渡（SDK 约束：speak 不可连续调用）。
   */
  ssml_chunks?: string[];
}

/** Agent 工具调用记录（P1 行动层） */
export interface ToolUsage {
  name: string;
  arguments: string;
  result: string;
}

/**
 * 本轮召回的记忆条数（按记忆分层）。
 * 字段可选：后端未来新增记忆层时不会破坏既有代码。
 */
export interface MemoryCounts {
  /** 情景记忆（对话片段的混合检索召回） */
  memories?: number;
  /** 语义事实（结构化三元组） */
  facts?: number;
}

/** POST /chat 响应 */
export interface ChatResponse {
  session_id: string;
  persona_id: string;
  reply: string;
  /**
   * 本轮情绪判定。
   * 后端 emotion 为 None 时返回空对象 `{}`，由 `api.ts` 规范化为 `null`
   * （否则调用方会拿到"既非 null、字段又全缺"的伪对象）。
   */
  emotion: EmotionInfo | null;
  system_prompt: string;
  messages: { role: string; content: string }[];
  worldbook_hits: string[];
  skipped: string[];
  memory_counts: MemoryCounts;
  /** 记忆写入已提交后台（响应时尚未落库；失败只记日志，不打断对话） */
  memory_scheduled: boolean;
  /** 本轮个人记忆（知识库）召回的文本行数 */
  knowledge_hits: number;
  warnings: string[];
  estimated_tokens: number;
  style: Record<string, unknown>;
  /** 本轮实际生效的模型预设（preset_id/preset_label/采样参数/enable_thinking） */
  preset?: Record<string, unknown>;
  /**
   * 本轮生效的 ST（酒馆）预设元信息；走内置分层路径时为 `{}`。
   * 字段：st_preset_id / st_preset_name / source_format / used_markers /
   * empty_markers / unresolved_macros / in_chat_count
   */
  st_preset?: Record<string, unknown>;
  /**
   * 播报指令。
   * 后端在 avatar 未启用或回复为空时返回空对象 `{}`，由 `api.ts` 规范化为 `null`。
   */
  speak: SpeakCommand | null;
  tools_used: ToolUsage[];
  note: string;
}

/** 模型预设档（与后端 app/llm/profiles.yaml 对应） */
export interface PresetInfo {
  id: string;
  label: string;
  description: string;
  match: string[];
  temperature: number;
  top_p: number | null;
  frequency_penalty: number | null;
  presence_penalty: number | null;
  max_tokens: number | null;
  /** 推理模型思考开关：null = 不传该参数（兼容普通模型） */
  enable_thinking: boolean | null;
}

/** GET /chat/presets 响应 */
export interface PresetCatalog {
  model: string;
  /** 按当前模型名自动匹配到的档位 id */
  auto_preset_id: string;
  presets: PresetInfo[];
}

/** 一条对话消息（前端展示用） */
export interface ChatMessage {
  /** 前端本地生成的稳定 id（用作列表 key；后端不感知） */
  id?: string;
  role: "user" | "assistant";
  text: string;
}

/** 具身状态机（赛题：Listen / Think / Speak / Interrupt） */
export type AvatarState = "idle" | "listen" | "think" | "speak";

/** 具身状态的中文标签与配色 */
export const AVATAR_STATE_LABELS: Record<AvatarState, string> = {
  idle: "待机",
  listen: "聆听中",
  think: "思考中",
  speak: "说话中",
};

// =============================================================
// 健康检查（GET /health）
// =============================================================

/** 单台 MCP 服务器的连接状态（后端 mcp/manager.py 的 status() 快照） */
export interface McpServerStatus {
  name: string;
  transport: string;
  connected: boolean;
  /** 该服务器暴露的工具名（未连接时为空数组） */
  tools: string[];
  /** 连接失败原因；未连接且无错误信息时为 null */
  error: string | null;
}

/** GET /health 响应（含 MCP 行动层连接状态） */
export interface HealthStatus {
  status: string;
  app: string;
  mcp: McpServerStatus[];
}

// =============================================================
// 个人记忆 / 知识库（backend/app/api/knowledge.py）
// =============================================================

/** 已入库文档（DocumentInfo） */
export interface KnowledgeDoc {
  doc_id: string;
  title: string;
  /** 来源类型：pdf / docx / md / txt / 粘贴文本 等（后端解析器给出） */
  source_type: string;
  chunk_count: number;
  /** ISO 8601 时间串（后端 datetime 序列化结果） */
  created_at: string;
}

/** POST /knowledge/upload 响应（UploadResult） */
export interface KnowledgeUploadResult {
  doc: KnowledgeDoc;
  /** 解析出的原始字符数 */
  parse_chars: number;
  /** 清洗后的字符数 */
  clean_chars: number;
  /** 被清洗移除的字符占比（0-1） */
  cleaned_ratio: number;
  /** 各类清洗规则命中次数 */
  clean_hits: Record<string, number>;
  /** 是否覆盖了已有文档（force=true 生效） */
  replaced: boolean;
}

/** DELETE /knowledge/{doc_id} 响应（DeleteResult） */
export interface KnowledgeDeleteResult {
  doc_id: string;
  removed_chunks: number;
}

/**
 * 上传判重命中详情（HTTP 409 的 detail 体）。
 * 后端**不会**自动覆盖：需带 force=true 重新提交。
 */
export interface KnowledgeDuplicateDetail {
  /**
   * 判重级别（后端 knowledge.py 的 kind 取值）：
   * file（同一文件）/ content（同内容不同格式）/ similar（近似重复）
   */
  reason: string;
  /** MinHash 近似相似度（file/content 级判重为 1） */
  similarity: number;
  existing: {
    doc_id: string;
    title: string;
    chunk_count: number;
  };
  hint: string;
}

// =============================================================
// 人设 / 文风清单（GET /chat/personas、GET /chat/styles）
// =============================================================

/** 一个人设（陪伴对象）——与后端 persona 预设 YAML 对应 */
export interface PersonaInfo {
  id: string;
  name: string;
  title: string;
  description: string;
  tags: string[];
}

/** GET /chat/personas 响应 */
export interface PersonaCatalog {
  /** 后端缺省人设 id（未显式传 persona_id 时生效） */
  default_persona_id: string;
  personas: PersonaInfo[];
}

/** 一种文风——与后端 style 预设 YAML 对应 */
export interface StyleOption {
  id: string;
  name: string;
  description: string;
  tags: string[];
  /** 示例对话组数（few-shot 数量，演示「反 AI 腔」时可提） */
  examples: number;
}

/** GET /chat/styles 响应 */
export interface StyleCatalog {
  default_style_id: string;
  styles: StyleOption[];
}

// =============================================================
// 会话历史（GET /chat/sessions、GET /chat/sessions/{id}/history）
// =============================================================

/** 会话列表项 */
export interface SessionSummary {
  session_id: string;
  persona_id: string;
  /** 首条用户消息的前若干字（无用户消息时为「新对话」） */
  title: string;
  message_count: number;
  /** ISO 8601 */
  updated_at: string;
}

/** 一条历史消息 */
export interface SessionMessage {
  role: string;
  text: string;
  created_at: string | null;
}

/** GET /chat/sessions/{id}/history 响应 */
export interface SessionHistory {
  session_id: string;
  persona_id: string;
  user_name: string;
  messages: SessionMessage[];
}

/** DELETE /chat/sessions/{id} 响应 */
export interface SessionDeleteResult {
  session_id: string;
  removed_turns: number;
}

// =============================================================
// SillyTavern 预设兼容（/chat/st-presets/*）
// 契约：docs/st-preset-compat.md；合规：预设正文属**用户本地数据**，不入库
// =============================================================

/** 一个 ST 预设条目（detail.prompts[]） */
export interface StPromptItem {
  identifier: string;
  name: string;
  role: string;
  content: string;
  /** 占位条目：正文由运行时填充，界面须置灰正文而非显示空文本框 */
  marker: boolean;
  system_prompt: boolean;
  forbid_overrides: boolean;
  /** 0 = Relative（跟顺序表）；1 = In-Chat（按 depth 插进历史） */
  injection_position: number;
  injection_depth: number;
  injection_order: number;
  /** 生成类型白名单（空 = 全部） */
  injection_trigger: string[];
  /** 本项目用什么内容填充该占位条目的说明（marker 条目非空） */
  marker_source: string;
  /** 正文是否可编辑（占位条目为 false） */
  content_editable: boolean;
}

/** 顺序表单项：条目是否启用（顺序即数组下标） */
export interface StPresetOrderEntry {
  identifier: string;
  enabled: boolean;
}

/** 记忆注入位置（契约 §7） */
export type StMemoryPosition =
  | "in_chat"
  | "in_prompt"
  | "world_info_before"
  | "world_info_after"
  | "off";

/** HyPRA 记忆层的注入配置（作为 ST 扩展注入参与同一 depth/order 的合并） */
export interface StMemoryInjection {
  enabled: boolean;
  position: StMemoryPosition;
  depth: number;
  role: string;
  order: number;
}

/** 预设清单项（**不含正文**） */
export interface StPresetSummary {
  id: string;
  name: string;
  source_file: string;
  imported_at: string;
  prompt_count: number;
  enabled_count: number;
  sampling: Record<string, number>;
  warnings: string[];
  /** 未生效特性（本项目不支持的预设功能，界面必须显式提示） */
  unsupported: string[];
  /** 是否存在界面编辑（覆盖层）；原始文件始终只读 */
  has_override: boolean;
  sha256: string;
}

/** GET /chat/st-presets 响应 */
export interface StPresetCatalog {
  /** 预设存放目录（用户本地路径，仅供排查） */
  dir: string;
  presets: StPresetSummary[];
}

/** 预设详情（GET /chat/st-presets/{id} 的 detail） */
export interface StPresetDetail {
  id: string;
  /** 能真正透传给 provider 的采样参数 */
  sampling: Record<string, number>;
  /** 仅保存与展示的扩展参数（当前模型忽略，界面须标注） */
  extended_sampling: Record<string, number>;
  assembly: {
    use_sysprompt: boolean;
    squash_system_messages: boolean;
    names_behavior: number;
  };
  order: StPresetOrderEntry[];
  prompts: StPromptItem[];
  memory_injection: StMemoryInjection;
  /** 当前覆盖层内容（调试/重置提示用） */
  override: Record<string, unknown>;
  /** chat | text（text = 仅采样参数可用） */
  source_format: string;
  /** 导入时为安全剥离的端点/密钥类字段名（值从未保存） */
  stripped_keys: string[];
  warnings: string[];
  unsupported: string[];
}

/** 导入 / 详情通用响应 */
export interface StPresetResponse {
  preset: StPresetSummary | { id: string };
  detail: StPresetDetail;
}

/** PATCH /chat/st-presets/{id} 的补丁（字段级深合并；值为 null = 删除该覆盖项） */
export interface StPresetPatch {
  sampling?: Record<string, number | null>;
  prompts?: Record<string, Record<string, unknown>>;
  prompt_order?: string[];
  memory_injection?: Record<string, unknown>;
  assembly?: Record<string, unknown>;
}
