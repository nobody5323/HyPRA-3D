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

/**
 * 一帧口型（`POST /media/avatar` 的 `visemes` 项）。
 *
 * `viseme` 取值（后端 `Viseme` 枚举，共 10 种）：
 * `sil` 静默 / `A` 大张口 / `I` 扁口 / `U` 圆口 / `E` 中开 / `O` 圆唇 /
 * `M` 闭唇 / `F` 唇齿 / `N` 舌尖 / `S` 齿音。
 */
export interface VisemeFrame {
  start_ms: number;
  end_ms: number;
  viseme: string;
  char: string;
}

/** 表情时间轴帧（后端 face） */
export interface FaceFrame {
  start_ms: number;
  end_ms: number;
  expression: string;
  intensity: number;
}

/** 动作时间轴帧（后端 body） */
export interface BodyFrame {
  start_ms: number;
  end_ms: number;
  gesture: string;
  intensity: number;
}

/**
 * 数字人驱动数据（`POST /media/avatar`）。
 *
 * 这是**渲染无关**的时间轴：口型 / 表情 / 动作 + 可选音频，
 * 既可以驱动魔珐 SDK，也可以驱动 Live2D（本项目就是这么用的）。
 */
export interface AvatarTimeline {
  text: string;
  provider: string;
  duration_ms: number;
  has_audio: boolean;
  audio_url: string | null;
  audio_format: string;
  visemes: VisemeFrame[];
  face: FaceFrame[];
  body: BodyFrame[];
  meta: Record<string, unknown>;
}

// =============================================================
// 语音引擎与音色（GET /media/tts/voices）
// =============================================================

/**
 * 一个可选音色（GPT-SoVITS 音色表里的一项）。
 *
 * 不含参考音频路径：那是**服务端**的文件路径，后端刻意不下发。
 */
export interface TtsVoiceInfo {
  id: string;
  label: string;
  /** 是否为未指定 voice 时的默认音色 */
  is_default: boolean;
}

/** 当前语音引擎状态（前端据此决定用服务端音频还是浏览器原生 TTS）。 */
export interface TtsVoicesStatus {
  /** 当前数字人驱动：local | xmov | gpt_sovits */
  provider: string;
  /** 该驱动是否产出服务端音频（false = 应回落浏览器 TTS） */
  server_tts: boolean;
  /** 服务端 TTS 是否已配好（false = 会降级为无音频） */
  configured: boolean;
  /** 默认音色 id（空 = 后端配置里的默认参考音频） */
  default_voice: string;
  voices: TtsVoiceInfo[];
  /**
   * 后端是否配了「情绪 → 音色」自动映射。
   *
   * 为 true 时「不指定音色」的含义是「按本轮情绪选声音」，
   * 前端据此把选项文案说清楚（否则用户会以为永远是同一个音色）。
   */
  emotion_voices: boolean;
  /** 状态说明 / 不可用原因（可直接展示给用户） */
  note: string;
}

// =============================================================
// 数字人模型库（/media/avatar/models/*）
// =============================================================

/** 模型类型：Live2D 模型包 | 静态立绘 */
export type AvatarModelKind = "live2d" | "images";

/** 模型库里的一个模型（后端返回的元数据） */
export interface AvatarModelInfo {
  id: string;
  name: string;
  kind: AvatarModelKind;
  createdAt: string;
  updatedAt: string;
  /**
   * live2d 专用：入口 `model3.json` **相对 files/ 目录**的路径。
   *
   * 取文件统一用 `/media/avatar/models/{id}/files/{entry}`；
   * 之所以也允许子目录（如 `CubismModel/pet.model3.json`），
   * 是因为模型包经常多套一层文件夹。
   */
  entry: string | null;
  /** live2d 专用：模型自带的表情名清单（供配置情绪→表情） */
  expressions: string[];
  /** images 专用：图片文件名清单（按上传顺序） */
  images: string[];
  /** images 专用：情绪 id → 图片文件名（由用户在前端逐个指定） */
  expressionMap: Record<string, string>;
  meta: Record<string, unknown>;
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

/** 插件在宿主中的状态（backend/app/plugins/manager.py 的 status()，见 AGENTS.md §9） */
export interface PluginStatus {
  id: string;
  display_name: string;
  version: string;
  /** 必需性分层：core 不可禁用，builtin 可禁用/替换，third-party 用户安装 */
  layer: "core" | "builtin" | "third-party";
  /** 能力分类（管理 UI 分组）：provider / tool / memory / datasource … */
  category: string;
  state: "discovered" | "loaded" | "started" | "disabled" | "failed";
  /**
   * 用户是否启用。与 `state` 是两个维度：`state` 是生命周期阶段，`enabled` 是用户意图。
   * core 层恒为 true——后端拒绝禁用它们（§9.2）。
   */
  enabled: boolean;
  /** 本插件贡献的能力面 */
  capabilities: string[];
  /** 只读插件（不写文件系统）：权限由后端架构强制，不是口头约定 */
  read_only: boolean;
  /** 加载/启动失败原因（仅 failed 时非空） */
  error: string;
}

/** 插件体系统计（/health 的 plugins_summary） */
export interface PluginsSummary {
  total: number;
  started: number;
  failed: number;
  by_layer: Record<string, number>;
}

export interface HealthStatus {
  status: string;
  app: string;
  mcp: McpServerStatus[];
  plugins: PluginStatus[];
  plugins_summary: PluginsSummary;
  /** 技能清单（见 AGENTS.md §9.6）；旧后端不返回时为空数组 */
  skills: SkillInfo[];
}

// =============================================================
// 插件管理（GET|PUT /plugins/…，backend/app/api/plugins.py）
// =============================================================

/** 单条配置项的 JSON Schema 声明（`manifest.settings_schema.properties` 的值）。 */
export interface PluginSettingField {
  type?: "string" | "number" | "integer" | "boolean" | "array" | "object";
  title?: string;
  description?: string;
  /** 给定候选项时渲染下拉框（优先于 type） */
  enum?: string[];
  /** 字符串展示格式；`password` 用密码框 */
  format?: string;
  default?: unknown;
  /** array / object 的元素声明 */
  items?: PluginSettingField;
  minimum?: number;
  maximum?: number;
}

/**
 * 插件配置的 JSON Schema（`manifest.settings_schema`）。
 *
 * 这里只声明宿主**真正会渲染**的子集。插件若用了更复杂的组合
 * （oneOf / allOf / $ref），表单会降级成只读 JSON 预览——不静默丢字段，
 * 用户至少看得到内容，也知道得手工改配置文件。
 */
export interface PluginSettingsSchema {
  type?: string;
  properties?: Record<string, PluginSettingField>;
  /** 必填字段名（表单打星号，缺值时阻止保存） */
  required?: string[];
}

/** 插件的权限声明（宿主强制校验，不是口头承诺，见 AGENTS.md §9.3） */
export interface PluginPermissions {
  filesystem: {
    /** 允许读取的路径白名单 */
    read: string[];
    /** 是否允许写文件系统；false 即「只读插件」 */
    write: boolean;
    write_paths: string[];
  };
  network: { hosts: string[] };
}

/** GET /plugins/{id}/settings */
export interface PluginSettingsResponse {
  id: string;
  /** 已保存的值；用户尚未填过时为空对象 */
  values: Record<string, unknown>;
  schema: PluginSettingsSchema;
  permissions: PluginPermissions;
}

/** GET /plugins/tavern-bridge/status：能读到什么 + 已导入进度 */
export interface TavernBridgeStatus {
  /** 生效的酒馆数据目录 */
  root: string;
  /** 可读内容的计数（entries / characters / sessions） */
  available: Record<string, number>;
  warnings: string[];
  /** 已导入到该陪伴对象的会话 id */
  imported_sessions: string[];
  characters: string[];
}

/** POST /plugins/tavern-bridge/import（backend/app/memory/tavern_import.py 的 ImportResult） */
export interface TavernImportResult {
  companion_id: string;
  sessions_total: number;
  sessions_imported: number;
  sessions_skipped: number;
  /** 成功写入的对话轮次 */
  turns: number;
  /** 抽取出的语义事实条数 */
  facts: number;
  /** 写入的情景记忆条数 */
  memories: number;
  warnings: string[];
}

// =============================================================
// 技能（/skills/*，backend/app/api/skills.py，见 AGENTS.md §9.6）
// =============================================================
//
// 技能与插件是**两套东西**：插件是「代码能力可插拔」（进程级），
// 技能是「方法论可插拔」（提示词级）——所以它们不共用一个界面区块。

/** 技能条目的元信息（不含正文——正文可能上万 token，按需单独拉）。 */
export interface SkillInfo {
  id: string;
  name: string;
  description: string;
  /** 什么情况下该用它（模型据此判断是否 study） */
  when_to_use: string;
  /** builtin（随项目分发）| user（自己放进 USER_SKILLS_DIR 的） */
  source: string;
  enabled: boolean;
  /** 正文长度（字符数），用来在列表里提示「这篇有多大」 */
  body_chars: number;
}

/** GET /skills/{id}：含 Markdown 正文 */
export interface SkillDetail {
  id: string;
  name: string;
  description: string;
  when_to_use: string;
  source: string;
  enabled: boolean;
  body: string;
}

/** GET /skills 与 POST /skills/reload 的统一响应 */
export interface SkillCatalog {
  skills: SkillInfo[];
  summary: { total: number; enabled: number };
}

// =============================================================
// 数字人模型来源（GET /media/avatar/models/sources，见 AGENTS.md §9.10 第 15 项）
// =============================================================
//
// 与 `listAvatarModels()` 的分工：那个只列**已装**的（可直接选用），
// 这个还回答「还有哪些可以获取」，并带作者与授权信息。

/** 来源级状态 */
export interface AvatarModelSourceStatus {
  id: string;
  /**
   * 是否**配了**来源位置。
   *
   * 注意它不等价于「读到东西了」：路径配了但文件读不到时也是 true，
   * 此时 `count` 为 0——所以界面要说「配了但没读到」，而不是「没配」。
   */
  available: boolean;
  description: string;
  count: number;
}

/** 一个可用模型（已装 / 可获取） */
export interface AvatarModelDescriptor {
  id: string;
  displayName: string;
  kind: AvatarModelKind;
  source: string;
  /** 已装进本机模型库（可直接选用）；false 表示「可获取」 */
  installed: boolean;
  entry: string;
  description: string;
  previewUrl: string;
  author: string;
  /** 授权说明 —— 模型不可自由分发，用户据此判断能不能用 */
  license: string;
  /** 获取地址（本项目只给链接，不代下载） */
  homepage: string;
  extra: Record<string, unknown>;
}

/** GET /media/avatar/models/sources */
export interface AvatarModelSources {
  sources: AvatarModelSourceStatus[];
  models: AvatarModelDescriptor[];
  installedCount: number;
  availableCount: number;
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
  /** true = 内置角色（用户自建角色为 false）；后端 /chat/personas 提供 */
  builtin?: boolean;
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
    /** 推理模式（对应酒馆的 show_thoughts）；null = 预设未提供，跟随内置档 */
    show_thoughts: boolean | null;
  };
  order: StPresetOrderEntry[];
  prompts: StPromptItem[];
  memory_injection: StMemoryInjection;
  /** 当前覆盖层内容（调试/重置提示用） */
  override: Record<string, unknown>;
  /** use_sysprompt 开启时替换系统条目正文的文本（空 = 未提供，按原正文发送） */
  system_prompt_override: string;
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
  /** use_sysprompt 开启时的覆盖文本（空串 = 清空） */
  system_prompt_override?: string;
}

// =============================================================
// AI 适配（POST /chat/st-presets/{id}/ai-adapt）
// 导入酒馆预设后，一键改造成本项目的陪伴对话形态
// =============================================================

/** 处置动作（与后端 rules.yaml 的 action 对应） */
export type StAdaptAction = "auto_patch" | "llm_rewrite" | "preserve" | "review";

/** 严重度（界面排序与配色用） */
export type StAdaptSeverity = "high" | "medium" | "low";

/** 一条体检发现 */
export interface StAdaptFinding {
  rule_id: string;
  severity: StAdaptSeverity;
  action: StAdaptAction;
  /** preset = 预设级字段；content = 条目正文 */
  scope: "preset" | "content";
  identifier: string;
  name: string;
  /** 命中的原文片段（已截断） */
  evidence: string;
  message: string;
  /** preserve 类：建议归拢到的槽位 */
  anchor: string;
}

/** 一条条目改写的处置结果 */
export interface StAdaptRewrite {
  identifier: string;
  name: string;
  status: "applied" | "rejected" | "missing" | "skipped";
  /** 拒绝原因，或采纳时的提示（如「改动幅度较大」） */
  reason: string;
  new_content: string;
}

/** 一条前后对照（供界面直接渲染） */
export interface StAdaptDiffEntry {
  scope: "preset" | "item" | "order";
  identifier: string;
  name: string;
  field: string;
  field_label: string;
  before: unknown;
  after: unknown;
  /** 仅 scope=order：位置发生变化的条目 */
  moved?: { identifier: string; name: string; from_index: number; to_index: number }[];
}

/** AI 适配结果 */
export interface StAdaptResult {
  preset_id: string;
  dry_run: boolean;
  applied: boolean;
  model_used: boolean;
  summary: Record<StAdaptSeverity, number>;
  findings: StAdaptFinding[];
  /** 内容保留、只调位置的条目（NSFW / 破限类） */
  preserved: StAdaptFinding[];
  rewrites: StAdaptRewrite[];
  diff: StAdaptDiffEntry[];
  patch: StPresetPatch;
  warnings: string[];
  /** 仅在 applied=true 时返回，供界面刷新编辑器 */
  detail?: StPresetDetail;
}

// =============================================================
// 对话模型的运行时切换（/llm/*）
// 分工：这里选**用哪个模型**；PresetInfo 配的是**这个模型的采样参数**
// =============================================================

/** 一个可选 provider（由后端给出，前端不再硬编码） */
export interface LlmProviderOption {
  value: string;
  label: string;
  hint: string;
}

/** 当前生效的模型配置（**不含 api_key 明文**） */
export interface LlmConfigInfo {
  provider: string;
  base_url: string;
  model: string;
  timeout: number;
  enable_thinking: boolean | null;
  /** 是否已保存 key —— 前端永远拿不到明文 */
  has_api_key: boolean;
  /** runtime = 界面设置；env = 部署配置；session = 本次运行生效但未落盘 */
  source: string;
}

/** GET /llm/config 响应 */
export interface LlmConfigResponse {
  config: LlmConfigInfo;
  providers: LlmProviderOption[];
  /** 各 provider 的默认端点（切 provider 时自动填入） */
  default_base_urls: Record<string, string>;
}

/** POST /llm/config/test 响应（测试连接） */
export interface LlmTestResult {
  ok: boolean;
  latency_ms: number;
  model: string;
  provider?: string;
  reply_preview?: string;
  error?: string;
}

/** 切换模型的请求体 */
export interface LlmConfigInput {
  provider: string;
  base_url?: string;
  model?: string;
  /** 留空 / 不传 = 沿用已保存的 key（前端拿不到明文） */
  api_key?: string | null;
  timeout?: number | null;
  enable_thinking?: boolean | null;
  /** 是否写入本地运行时配置（重启后保留） */
  persist?: boolean;
}

// =============================================================
// 创作工坊：用户自建角色卡 / 世界书条目（backend/app/api/studio.py）
// =============================================================

/** 角色清单项（**不含**人设正文——正文要调详情接口） */
export interface StudioPersonaSummary {
  id: string;
  name: string;
  title: string;
  description: string;
  tags: string[];
  /** true = 内置角色（只读，可「复制为我的角色」） */
  builtin: boolean;
}

/** 角色卡详情（含正文，用于编辑） */
export interface StudioPersona extends StudioPersonaSummary {
  /** 人设正文（支持 {{user_name}} 等状态变量宏） */
  prompt: string;
  /** 背景故事（常驻注入，排在人设正文之后、世界书之前） */
  background: string;
  /** 声明用到的状态变量名（写作提示，仅展示） */
  variables: string[];
  creator: string;
}

/** 世界书条目（内置 + 我的；内置只读） */
export interface StudioWorldBookEntry {
  id: string;
  title: string;
  /** 归属：`*` = 对所有陪伴对象生效；或一个角色 id = 只对该角色生效 */
  scope: string;
  content: string;
  /** 关键词（任一命中即触发） */
  keys: string[];
  /** 正则（任一命中即触发） */
  regex: string[];
  /** 语义触发文本（非空即启用向量通道） */
  vector_text: string;
  vector_threshold: number;
  /** 是否参与触发（内置条目的开关是**用户侧**的停用偏好） */
  enabled: boolean;
  case_sensitive: boolean;
  /** 注入优先级（越大越靠前） */
  priority: number;
  /** true = 内置条目（只读，可复制或停用） */
  builtin: boolean;
}

/** 状态变量写作提示（catalog.state_vars） */
export interface StudioStateVarHint {
  name: string;
  description: string;
  default: string;
  example: string;
}

/** 字段长度上限（后端 LIMITS）：界面用它做即时校验，避免前后端两套数字 */
export type StudioLimits = Record<string, number>;

/** GET /chat/studio/catalog 响应（工坊的一次性加载） */
export interface StudioCatalog {
  personas: StudioPersonaSummary[];
  entries: StudioWorldBookEntry[];
  /** 读盘警告（用户手改的坏文件被跳过等），界面应展示给用户 */
  warnings: string[];
  limits: StudioLimits;
  /** 归属通配值（后端 SCOPE_ALL，通常为 "*"） */
  scope_all: string;
  state_vars: StudioStateVarHint[];
}

/** 角色写入响应（带回刷新后的 catalog，界面不必再拉一次） */
export interface StudioPersonaWriteResult {
  persona: StudioPersona;
  catalog: StudioCatalog;
}

/** 世界书条目写入响应 */
export interface StudioEntryWriteResult {
  entry: StudioWorldBookEntry;
  catalog: StudioCatalog;
}

/** 删除角色卡的响应（**只删角色卡**，会话与记忆保留） */
export interface StudioPersonaDeleteResult {
  deleted: string;
  name: string;
  /** 该角色名下还有多少段对话 */
  sessions: number;
  /** 有多少世界书条目只对该角色生效 */
  bound_entries: number;
  /** 后端给出的「什么被留下了」说明 */
  kept: string;
  catalog: StudioCatalog;
}

/** 新建角色卡的请求体（更新时传它的子集） */
export interface StudioPersonaInput {
  name: string;
  prompt: string;
  title?: string;
  description?: string;
  background?: string;
  tags?: string[];
  variables?: string[];
}

/** 世界书条目的请求体（更新时传它的子集） */
export interface StudioEntryInput {
  title: string;
  content: string;
  scope?: string;
  keys?: string[];
  regex?: string[];
  vector_text?: string;
  vector_threshold?: number;
  case_sensitive?: boolean;
  priority?: number;
  enabled?: boolean;
}

/** 「试触发」请求体：草稿字段 + 一段样例文本（不落盘） */
export interface StudioEntryTestInput {
  /** 用来试触发的样例文本（模拟用户会说的话） */
  text: string;
  /** 按哪个陪伴对象试：`*` 模拟全局，或填角色 id 模拟该角色的会话 */
  companion_id?: string;
  title?: string;
  content?: string;
  keys?: string[];
  regex?: string[];
  vector_text?: string;
  vector_threshold?: number;
  case_sensitive?: boolean;
}

/** 「试触发」结果：**逐通道**给出结论，用户才知道该怎么改 */
export interface StudioEntryTestResult {
  matched: boolean;
  /** 命中的关键词 */
  keys_hit: string[];
  /** 命中的正则模式 */
  regex_hit: string[];
  /** 语义相似度（未声明语义文本、或计算失败时为 null） */
  vector_score: number | null;
  vector_threshold: number;
  scope: string;
  /** 命中时会注入的文本（未命中为空串） */
  injected_text: string;
  estimated_tokens: number;
  warnings: string[];
}
