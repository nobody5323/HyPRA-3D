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
  /**
   * 本轮实际生效的交互模式：`companion`（桌宠对话）/ `tavern`（酒馆聊天）。
   *
   * 界面据此解释「这轮为什么没有酒馆世界书」——酒馆来源的知识只在 `tavern`
   * 模式召回（见 `app/memory/knowledge/scopes.py`）。字段可选：老后端没有它。
   */
  mode?: string;
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
   * 本轮生效的叙事框架层（jailbreak）元信息；未启用时为 `{}`。
   * 字段：jailbreak_id / jailbreak_name / intensity / requires_adult。
   *
   * 界面据此显示「这轮开着哪个叙事框架」——用户开了它就该看得见，
   * 否则会分不清「回复风格变了」是模型的问题还是自己开了开关。
   */
  jailbreak?: Record<string, unknown>;
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

/** 当前语音引擎状态（前端据此决定用服务端音频还是静默）。 */
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
// 数字人凭证（GET|PUT /media/avatar/credentials）
// =============================================================
//
// 凭证存**后端**而不是 localStorage：三个界面的 origin 不同
// （控制台 / 桌宠窗是 127.0.0.1:34567，Web 端是 localhost:3000），
// localStorage 天然不共享，而「用哪套密钥」必须是三处一致的事实。

/**
 * 凭证形态。
 *
 * 为什么分两种：魔珐的横屏/竖屏是**控制台创建应用时**定下的，容器比例必须与
 * 应用类型一致（实测同一个 appId 换比例会变形）。Web 端用横屏应用（`web`）、
 * 桌宠窗用竖屏应用（`pet`），所以凭证按形态分开存。
 */
export type AvatarCredentialForm = "web" | "pet";

/**
 * 某形态的凭证与来源。
 *
 * `source` 三档：`user`（界面填的，存后端）> `env`（部署配置 `.env`）> `none`。
 */
export interface AvatarCredentialsResponse {
  form: AvatarCredentialForm;
  appId: string;
  appSecret: string;
  source: "user" | "env" | "none";
  configured: boolean;
}

// =============================================================
// 数字人模型库（/media/avatar/models/*）
// =============================================================

/** 模型类型：Live2D 模型包 | 静态立绘 */
export type AvatarModelKind = "live2d" | "images";

/**
 * 构图校准参数。
 *
 * 存在的理由：不同模型的画布比例与人物在画布中的位置差别很大（半身 / 全身 / 偏移），
 * 用同一套默认构图必然有的模型显示不全。校准值随模型存**后端元数据**，
 * 这样 Web 端与桌面端读到的是同一份（见 `AvatarModelInfo.layout`）。
 *
 * 它是服务端契约，所以定义在本文件；渲染层的 `AvatarPortraitLayout` 只是别名。
 */
export interface AvatarLayout {
  /** 缩放（1 = 原始） */
  scale?: number;
  /** 水平位移（px，正值向右） */
  offsetX?: number;
  /** 垂直位移（px，正值向下） */
  offsetY?: number;
  /** CSS `transform-origin`，如 "bottom center" */
  anchor?: string;
}

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
  /** 构图校准；`null` = 未校准（渲染层用默认构图） */
  layout: AvatarLayout | null;
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
  /** 一句话说明（能力中心在详情里展示：告诉用户这个能力是什么、开关在哪） */
  description: string;
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
  /**
   * 表单初始值：已保存的值 + 未保存字段的「宿主当前生效值」（.env / 运行时覆盖）。
   * **密钥字段永远不在这里**（见 `secretsSet`）。
   */
  values: Record<string, unknown>;
  /**
   * 已配置的密钥字段名（明文永不下发）：
   * 前端据此显示「已保存，留空则沿用」，留空提交 = 沿用，提交 `null` = 清除。
   */
  secrets_set: string[];
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
  /** 旧兼容字段：酒馆世界书不再直接注入提示词，当前固定为 false */
  worldbook_enabled: boolean;
  /** 还没同步进记忆的对话轮次数（与点同步后实际写入的轮次同一套规则算出） */
  pending_turns: number;
  /** 有新增内容的会话数 */
  pending_sessions: number;
  /** 已同步进个人知识库的酒馆世界书规模（按需召回的那条路） */
  knowledge: TavernKnowledgeStatus;
  /** 可选的世界书来源（勾选决定它是否参与接入） */
  books: TavernBookOption[];
  /**
   * 当前**已挂载**的世界书来源（决定酒馆模式下查哪几本）。
   *
   * 与 `books` 是两回事：`books` 管「要不要读进知识库」，`mounted_books` 管
   * 「读进来的哪几本允许被检索」。**默认空 = 一本都不查**。
   */
  mounted_books: string[];
}

/** 一个可选的世界书来源（世界书文件 / 某个角色的内嵌设定） */
export interface TavernBookOption {
  /** 来源标识：`world/<文件名>` 或 `char/<角色名>` */
  source: string;
  /** 界面展示名 */
  label: string;
  entries: number;
  enabled: boolean;
}

/** 知识库里由酒馆世界书同步来的规模（GET /plugins/tavern-bridge/status 的 knowledge 字段） */
export interface TavernKnowledgeStatus {
  documents: number;
  chunks: number;
  scopes: number;
}

/**
 * GET /plugins/tavern-bridge/mounted-books：挂载清单 + 全部可选来源。
 *
 * 挂载是**全局当前挂载**（一份）：切换世界书就是改这份清单，
 * 只有挂上的书才参与知识检索（每本一个独立作用域，互不串味）。
 */
export interface TavernMountedBooks {
  /** 当前已挂载的来源（只含仍然存在的） */
  mounted: string[];
  /** 全部可选来源及各自的挂载状态 */
  available: TavernMountedEntry[];
  /** PUT 时被丢弃的无效来源（已不存在于酒馆） */
  dropped?: string[];
}

/** 挂载清单里的一项 */
export interface TavernMountedEntry {
  /** 来源标识：`world/<文件名>` 或 `char/<角色名>` */
  source: string;
  mounted: boolean;
}

/** POST /plugins/tavern-bridge/knowledge/sync */
export interface TavernKnowledgeSyncResult {
  /** 生效的酒馆数据目录 */
  root: string;
  documents: number;
  chunks: number;
  /** 清掉的「来源已不存在」文档数 */
  removed: number;
  /** 因找不到所属角色卡而没进库的条目数 */
  skipped: number;
  scopes: Record<string, number>;
  warnings: string[];
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
  /** 每个陪伴对象写入了多少轮：酒馆里不同角色各进各的记忆 */
  scopes: Record<string, number>;
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
  /**
   * 是否已被删除。
   *
   * 内置技能的「删除」只是写进用户侧隐藏清单（包内文件一字未改），因此清单里
   * 仍然带着它、并置此标记——界面靠它列出「已隐藏」并提供恢复入口。
   * 自建技能是物理删除，不会再出现在清单里。
   */
  deleted: boolean;
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
  /** **含隐藏项**（`deleted: true`）：界面要能列出「已隐藏」才给得出恢复入口 */
  skills: SkillInfo[];
  /** total / enabled 只数可见技能；hidden 单独报（旧后端不返回时按 0 处理） */
  summary: { total: number; enabled: number; hidden: number };
}

/** POST /skills：新建技能的返回值（落盘后的 id / 路径 + 刷新过的清单） */
export interface SkillWriteResponse extends SkillCatalog {
  id: string;
  path: string;
}

/** DELETE /skills/{id}：删掉了什么 + 刷新过的清单 */
export interface SkillDeleteResult extends SkillCatalog {
  deleted: string;
  name: string;
  source: string;
  /** true = 内置技能只是被隐藏（文件仍在 `backend/skills/`，可恢复） */
  hidden: boolean;
  /** 磁盘上被删掉的路径；内置技能为 null（什么都没从磁盘上消失） */
  path: string | null;
}

/** POST /skills/{id}/restore：恢复被隐藏的内置技能 */
export interface SkillRestoreResult extends SkillCatalog {
  restored: string;
  name: string;
}

// =============================================================
// LLM 辅助创作（人设 / 技能 / 插件，AGENTS.md §9.12）
// =============================================================
//
// 三者的流程一致：**生成草稿（不落盘）→ 用户在界面上审阅编辑 → 显式保存**。
// 因此 `/ai-draft` 一律只返回内容，不产生任何副作用。

/** 一次 AI 草稿的返回：内容 + 用的模型（界面要显示「谁写的」） */
export interface AiDraftResponse<T> {
  draft: T;
  model: string;
}

/** 人设草稿（字段与创作工坊的表单一一对应） */
export interface PersonaAiDraft {
  name: string;
  title: string;
  description: string;
  tags: string[];
  prompt: string;
  background: string;
  variables: string[];
}

/** 技能草稿（字段与 SKILL.md 的 frontmatter 一一对应） */
export interface SkillAiDraft {
  id: string;
  name: string;
  description: string;
  when_to_use: string;
  body: string;
}

/** POST /skills 的请求体 */
export interface SkillCreateInput {
  id: string;
  name: string;
  description?: string;
  when_to_use?: string;
  body: string;
}

/**
 * 插件扫描目录（GET /plugins 的 `dirs`）。
 *
 * 插件由**用户自己写**：把 `<id>/manifest.json` + `plugin.py` 放到 `userDir` 下点
 * 「重新扫描」即可；开发期也可以把插件所在目录填进 `PLUGIN_EXTRA_DIRS`（`extraDirs`），
 * 宿主直接扫它、不碰源文件。接口说明见 `docs/plugin-development.md`。
 */
export interface PluginDirs {
  /** 用户插件目录：把插件目录丢进来 + 重新扫描（最常见的一条路） */
  user_dir: string;
  /** 额外来源目录（`PLUGIN_EXTRA_DIRS`，逗号分隔）：开发期零拷贝接入 */
  extra_dirs: string[];
  /** 第一方插件目录（随项目分发，只读展示） */
  builtin_dir: string;
}

/** GET /plugins 的返回 */
export interface PluginListResponse {
  plugins: PluginStatus[];
  summary: PluginsSummary;
  /** 能力名 → 提供者插件 id 列表 */
  capabilities: Record<string, string[]>;
  dirs: PluginDirs;
}

/** POST /plugins/import 的请求体 */
export interface PluginImportInput {
  /** 插件源目录（其下须有 manifest.json） */
  path: string;
  /** 目标已存在时是否整体替换（默认拒绝） */
  replace?: boolean;
}

/** POST /plugins/import 的返回 */
export interface PluginImportResponse {
  id: string;
  /** 导入后的落盘目录 */
  path: string;
  enabled: boolean;
  plugins: PluginStatus[];
}

/** POST /plugins/reload 的返回 */
export interface PluginReloadResponse {
  /** 本次新发现的插件 id */
  discovered: string[];
  plugins: PluginStatus[];
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

// -------------------------------------------------------------
// 叙事框架（jailbreak）预设（GET /chat/jailbreak-presets）
// -------------------------------------------------------------
//
// 与文风、人设**正交**：人设管「是谁」，文风管「怎么说话」，本层管
// 「这段对话处在什么框架里」（虚构叙事 / 现实问答）。三者可以任意组合。
//
// 这一层**出厂默认关闭**：它会改写模型的回应框架，属于用户知情后自行开启的能力。
// 因此响应里同时带 `enabled`（部署默认）与预设清单，界面据此决定是否渲染开关。
//
// 注意：`requires_adult` 为 true 的档位只应展示给已确认成年的用户。后端不拦截
// （该字段只做声明），确认动作由前端负责——本组件用一层本地确认拦住它。

/** 一个叙事框架档位——与后端 jailbreak 预设 YAML 对应 */
export interface JailbreakOption {
  id: string;
  name: string;
  description: string;
  tags: string[];
  /** 强度 0~2（0 只声明虚构语境，2 在虚构语境内放开题材） */
  intensity: number;
  /** 强度的中文说明（由后端 `INTENSITY_LABELS` 给出，前端不自行翻译） */
  intensity_label: string;
  /** true = 含成人向内容，展示前需要用户确认成年 */
  requires_adult: boolean;
}

/** GET /chat/jailbreak-presets 响应 */
export interface JailbreakCatalog {
  /** 部署默认是否启用（`JAILBREAK_ENABLED`）；用户选择在 /chat/preferences 里 */
  enabled: boolean;
  /** 部署声明的缺省档位 id（`JAILBREAK_PRESET`，为空时后端给保守档） */
  default_jailbreak_id: string;
  presets: JailbreakOption[];
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

/** DELETE /chat/sessions?persona_id=… 响应（清空该角色的全部会话） */
export interface SessionPurgeResult {
  persona_id: string;
  removed_sessions: number;
  removed_turns: number;
}

/**
 * DELETE /chat/memory?persona_id=… 响应（清空该角色的全部记忆）。
 *
 * `removed` 的键固定为 warm / facts / mood_log / knowledge，
 * 值是该层被删掉的条数（分块数 / 记忆条数）。
 */
export interface MemoryPurgeResult {
  persona_id: string;
  removed: Record<string, number>;
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
  /** true = 内置角色（不可直接编辑，但可从当前工坊删除或复制） */
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

/** 世界书条目（内置 + 我的；内置不可直接编辑，但可停用或删除） */
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
  /** true = 内置条目（不可直接编辑，可停用或删除） */
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

/** 文风的采样参数白名单项（键名与取值区间由后端给出，界面不硬编码） */
export interface StudioSamplingSpec {
  key: string;
  min: number;
  max: number;
}

/** GET /chat/studio/catalog 响应（工坊的一次性加载） */
export interface StudioCatalog {
  personas: StudioPersonaSummary[];
  entries: StudioWorldBookEntry[];
  styles: StudioStyleSummary[];
  /** 读盘警告（用户手改的坏文件被跳过等），界面应展示给用户 */
  warnings: string[];
  limits: StudioLimits;
  /** 文风可用的采样参数（键名 / 区间），界面据此渲染输入框与即时校验 */
  sampling_spec: StudioSamplingSpec[];
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

/** 删除世界书条目的响应（内置条目也是从当前工坊隐藏，不修改包内资源） */
export interface StudioEntryDeleteResult {
  deleted: string;
  title: string;
  catalog: StudioCatalog;
}

/**
 * 删除角色卡的响应。
 *
 * **级联删除**：该角色的全部会话、四层记忆（情景 / 事实 / 情绪日记 / 个人语料）
 * 与专属世界书条目都会一并清除，不可恢复——界面必须先做二次确认。
 */
export interface StudioPersonaDeleteResult {
  deleted: string;
  name: string;
  /** 被清掉的对话段数 */
  removed_sessions: number;
  /** 被清掉的对话消息条数 */
  removed_turns: number;
  /** 被清掉的专属世界书条目数（scope = 该角色 id） */
  removed_entries: number;
  /** 各层记忆被清掉的条数 */
  removed_memory: {
    warm: number;
    facts: number;
    mood_log: number;
    knowledge: number;
  };
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

// =============================================================
// 文风预设（GET /chat/studio/styles*）
// =============================================================
//
// 文风与角色**正交**：人设管「是谁」，文风管「怎么说话」，所以一份文风可以配任意
// 角色。也因此它没有归属、删除也没有级联——删掉一份文风不会牵连会话或记忆。
//
// 选择器用的清单（`GET /chat/styles` 的 `StyleOption`）刻意只含展示字段；
// 工坊要编辑正文，所以这里另有一套带 `style_prompt` 的类型。

/** 一组示例对话（few-shot 示范目标语气） */
export interface StyleExample {
  user: string;
  assistant: string;
}

/** 文风预设清单项（不含风格指令与示例正文） */
export interface StudioStyleSummary {
  id: string;
  name: string;
  description: string;
  tags: string[];
  /** 示例对话组数（详情里的 `examples` 是数组，故此处刻意不同名） */
  example_count: number;
  /** true = 内置文风（不可直接编辑，但可复制或从当前工坊删除） */
  builtin: boolean;
}

/** 文风预设详情（含风格指令与示例，用于编辑） */
export interface StudioStyle extends StudioStyleSummary {
  /** 正向风格指令（放 system 末尾，影响更强） */
  style_prompt: string;
  /** 要避免的表达（克制使用，只列最刺眼的 AI 腔） */
  avoid: string[];
  /** 示例对话（few-shot，反 AI 腔最有效的手段） */
  examples: StyleExample[];
  /** 建议采样参数（只认后端白名单里的键） */
  sampling: Record<string, number>;
  /** 与人设冲突的特质关键词（用于一致性告警，不阻断） */
  conflicts_with: string[];
}

/** 新建 / 更新文风的请求体 */
export interface StudioStyleInput {
  name: string;
  style_prompt: string;
  description?: string;
  tags?: string[];
  avoid?: string[];
  examples?: StyleExample[];
  sampling?: Record<string, number>;
  conflicts_with?: string[];
}

/** 文风写入响应（带回刷新后的 catalog，界面不必再拉一次） */
export interface StudioStyleWriteResult {
  style: StudioStyle;
  catalog: StudioCatalog;
}

/** 删除文风的响应 */
export interface StudioStyleDeleteResult {
  deleted: string;
  name: string;
  catalog: StudioCatalog;
}
