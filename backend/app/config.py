"""应用配置：从 .env 读取运行参数与云端密钥。

红线：云端密钥（百炼 / 硅基流动 / 魔珐星云 / Qdrant）一律只放 .env
（已被 .gitignore 排除），不入库。格式参考 backend/.env.example。
开发形态下 .env 在 backend/；打包形态下在用户数据目录（见 app/paths.py）。

双模式（配置驱动，代码零硬编码）：
- 开发：向量库用云 Qdrant、模型用云 key；
- 评审：docker compose 本地 Qdrant（QDRANT_URL=http://qdrant:6333），
  embedding/LLM 由评审在 .env 自行填入（provider/key/model 均走配置）。
"""

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.paths import config_file, data_root, resolve_under, resource_root

logger = logging.getLogger(__name__)

# 本文件位于 backend/app/config.py；开发形态下 .env 约定在 backend/.env。
# ⚠️ pydantic-settings 的 env_file 在**类定义时**求值，因此这里只能是模块级
#    常量；其余路径字段由下面的 model_validator 在**每次构造 Settings 时**解析。
_ENV_FILE = config_file()

#: 可写数据类路径字段：相对值按 data_root() 解析（数据库 / 用户内容 / 产物）。
#: 集中成一个元组，避免 validator 里散落一长串字符串字面量。
_DATA_PATH_FIELDS = (
    "cold_db_path",
    "session_db_path",  # 空串 = 跟随冷层库文件，跳过
    "st_presets_dir",
    "studio_dir",
    "plugins_dir",
    "tavern_import_state_file",
    "user_skills_dir",
    "skills_state_file",
    "avatar_models_dir",
    "chat_preferences_path",
    "avatar_credentials_path",
    "media_dir",
    "gpt_sovits_voices_file",
    # 感知层：ASR 模型权重（**不进发布包**，见 docs/proactive-multimodal.md §4.3）
    "asr_model_dir",
    "asr_download_root",  # 空串 = 不启用自动下载，跳过
    # 主动链路：节流状态（上次主动开口时刻 / 每日计数 / 触发器上次触发日）
    "proactive_state_path",
)

#: 只读资源类路径字段：相对值按 resource_root() 解析（随程序分发的内容）。
_RESOURCE_PATH_FIELDS = (
    "builtin_plugins_dir",
    "skills_dir",
)


# =============================================================
# 运行时配置覆盖层（界面设置 > .env）
# =============================================================
#
# 为什么需要这一层：插件体系（AGENTS.md §9.5）让插件用 JSON Schema 声明配置、
# 宿主渲染表单，配置落在 `data/plugins/<id>/settings.json`。但「声明了」不等于
# 「生效了」——本层负责把它接到运行时：**插件 schema 的键名与 Settings 字段同名时，
# 宿主把该值作为运行时覆盖**，语义与对话模型的「界面设置（runtime）> 部署配置（env）」
# 一致（见 app/llm/runtime.py）。
#
# 为什么用白名单（而不是「插件声明什么就能改什么」）：settings.json 是用户可手改的
# 普通文件，若允许任意键覆盖，一个笔误就能改掉 CORS 白名单这类宿主配置。
# 因此**能覆盖哪些字段由宿主决定**，插件只能在白名单内生效（越界键忽略并告警）。
#
# 与 `app/llm/runtime.py` 的边界：对话模型走它自己那套（本地运行时文件 + 连通性测试
# + 模型列表拉取），两者互不覆盖；本层服务于插件的声明式配置。
RUNTIME_OVERRIDABLE_FIELDS = frozenset(
    {
        # 文本向量化（embedding 插件）
        "embedding_provider",
        "embedding_api_key",
        "embedding_model",
        "embedding_base_url",
        "embedding_dim",
        "embedding_timeout",
        # 语音合成（tts 插件）
        "gpt_sovits_base_url",
        "gpt_sovits_ref_audio",
        "gpt_sovits_prompt_text",
        "gpt_sovits_prompt_lang",
        "gpt_sovits_text_lang",
        "gpt_sovits_speed",
        "gpt_sovits_media_type",
        "gpt_sovits_timeout",
        "gpt_sovits_extra_params",
        "gpt_sovits_warmup",
        "gpt_sovits_voices_file",
        "gpt_sovits_default_voice",
        # 酒馆世界书注入开关（tavern-bridge 插件）
        "tavern_worldbook_enabled",
        # 启动时增量同步酒馆新对话（tavern-bridge 插件）
        "tavern_sync_on_startup",
        # 注入预算：酒馆资料接得多时用户需要自己调（§9.5 的声明式配置）
        "worldbook_budget",
        "knowledge_context_budget",
        "memory_layer_budget",
        "prompt_history_budget",
        "prompt_total_budget",
        # 单次回复字数上限（0 = 不限制）
        "reply_char_limit",
        # 叙事框架层（jailbreak）：开关与档位必须能在界面上切，
        # 否则「打开破限」就得改 .env 重启，和「文风/人设随选随生效」不一致
        "jailbreak_enabled",
        "jailbreak_preset",
        "jailbreak_budget",
        # ---- 感知层（见 docs/proactive-multimodal.md §4）----
        # ASR：能力中心里可直配（换模型 / 换设备 / 开关麦克风）
        "asr_provider",
        "asr_model",
        "asr_model_dir",
        "asr_device",
        "asr_compute_type",
        "asr_beam_size",
        "asr_language",
        "asr_warmup",
        "asr_vad_filter",
        "asr_no_speech_threshold",
        "asr_ui_enabled",
        "asr_shortcut",
        # 桌面情景：窗口标题是敏感项，必须能在界面上单独关（§4.6）
        "perception_desktop_enabled",
        "perception_include_window_title",
        "perception_ttl_seconds",
        # ---- 时间感 / 行踪 / 偏好画像（§4.8 / §4.9）----
        # 行踪是最敏感的一项（它是**历史**，不只是「此刻」），
        # 因此开关、保留期、是否记标题、空闲阈值全部开放给用户——
        # 「你能决定它记住多少」是这一层能被接受的前提
        "perception_include_time",
        "perception_budget",
        "perception_activity_enabled",
        "perception_activity_recent_hours",
        "perception_activity_retention_days",
        "perception_activity_include_title",
        "perception_activity_idle_gap_seconds",
        "perception_activity_max_segment_hours",
        "perception_profile_enabled",
        "perception_profile_days",
        "perception_profile_min_minutes",
        # ---- 图片理解（Qwen2.5-VL，§4.4）----
        # 视觉模型与对话模型**分开配**（见 app/perception/vision/base.py 的说明）：
        # 绑在一起的话，没配 VL 模型就会连聊天都用不了
        "vision_provider",
        "vision_api_key",
        "vision_model",
        "vision_base_url",
        "vision_timeout",
        "vision_max_image_mb",
        "vision_prompt",
        "vision_ui_enabled",
        # ---- 主动链路（见 §5.3）----
        # 七道闸门里的参数都开放给用户：它们是「被骚扰」与「不打扰」的分界，
        # 让用户只能接受出厂值等于把这个选择权拿走了
        "proactive_enabled",
        "proactive_daily_quota",
        "proactive_min_interval_minutes",
        "proactive_quiet_hours",
        "proactive_quiet_exempt_care",
        "proactive_daily_time",
        "proactive_idle_hours",
        "proactive_memory_followup_days",
        "proactive_back_from_away_minutes",
        "proactive_activity_shift_minutes",
        "proactive_reply_char_limit",
    }
)

#: 覆盖值（字段名 → 已按字段类型转换的值）。**只由 replace_runtime_overrides 改写**。
_runtime_overrides: dict[str, Any] = {}


class _Invalid:
    """「值无法解析」的哨兵（与 None 区分：None 可能是合法配置）。"""


_INVALID = _Invalid()


def runtime_overrides() -> dict[str, Any]:
    """当前覆盖快照（副本；调用方比对前后差异时用）。"""
    return dict(_runtime_overrides)


def replace_runtime_overrides(values: Mapping[str, Any]) -> bool:
    """**全量替换**覆盖层，返回是否真的发生了变化。

    全量替换（而非增量合并）是刻意的：调用方是插件宿主，它每次都从「当前启用插件」
    重新收集一遍——被禁用/被删掉的插件才不会留下上一轮的残留覆盖。

    越界键、不存在的字段与无法转换的值一律忽略并告警：配置问题只该让「覆盖不生效」，
    不该让宿主起不来。告警只针对「试图覆盖宿主配置但没被开放」的字段——
    插件自己的私有配置项（如 `tavern_dir`）不是 Settings 字段，静默跳过即可。
    """
    resolved: dict[str, Any] = {}
    for field, value in values.items():
        if field not in Settings.model_fields:
            continue
        if field not in RUNTIME_OVERRIDABLE_FIELDS:
            logger.warning("忽略未开放的宿主配置字段：%s（见 RUNTIME_OVERRIDABLE_FIELDS）", field)
            continue
        coerced = _coerce_override(field, value)
        if coerced is _INVALID:
            logger.warning("配置字段 %s 的值无法解析，已忽略：%r", field, value)
            continue
        resolved[field] = coerced

    global _runtime_overrides
    if resolved == _runtime_overrides:
        return False
    _runtime_overrides = resolved
    return True


def clear_runtime_overrides() -> bool:
    """清空覆盖层（回到纯 .env）。返回是否真的有内容被清掉。"""
    global _runtime_overrides
    had = bool(_runtime_overrides)
    _runtime_overrides = {}
    return had


def effective_overridable_values(fields: Iterable[str]) -> dict[str, Any]:
    """这些字段**当前生效**的值（覆盖层 > .env > 默认值）。

    用途：管理 UI 的配置表单要用「实际在跑的值」回填，否则用户看到一个空表单，
    无法判断当前到底用的是哪个模型 / 哪个端点。

    ⚠️ 返回值**可能含密钥**（调用方是宿主内部）：不得直接回传前端——
    密钥字段的展示口径见 `app/plugins/settings_runtime.py`（只回「有没有」）。
    """
    settings = Settings()
    return {
        name: getattr(settings, name)
        for name in fields
        if name in RUNTIME_OVERRIDABLE_FIELDS and name in Settings.model_fields
    }


def _coerce_override(field: str, value: Any) -> Any:
    """按 Settings 声明的类型转换界面值。

    为什么必须转：覆盖是在 model_validator 里 `setattr` 写入的，**绕过了 pydantic
    校验**，JSON 里存成字符串的数字会原样漏到下游（如 `embedding_dim="1024"`
    直接进 Qdrant 建 collection）。
    """
    annotation = Settings.model_fields[field].annotation
    try:
        if annotation is bool:
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if annotation is int:
            return int(value)
        if annotation is float:
            return float(value)
    except (TypeError, ValueError):
        return _INVALID
    return str(value)


class Settings(BaseSettings):
    """全局配置。新增配置项时在此声明字段，并在 .env / .env.example 同步。"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 基础 ----
    app_name: str = "HyPRA Backend"
    app_version: str = "0.1.0"
    debug: bool = False

    # ---- CORS（前端跨域访问）----
    # 逗号分隔的允许来源；"*" 表示允许全部（仅建议本地/内网演示使用）
    # 默认覆盖本地开发（Next.js 3000）与 docker 部署场景
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8000,http://127.0.0.1:8000"
    )
    # 追加的正则白名单：本机回环地址下的**任意端口**。
    #
    # 为什么需要它：前端不一定跑在 3000——桌面端（Electron）内置的静态资源服务
    # 从 34567 起找可用端口，Web 端也可能改端口。端口写死会让这些来源被 CORS 拦掉，
    # 表现为界面一直「后端未连接」（curl 却正常，所以很难定位）。
    # 置空字符串可关闭（只用上面的固定列表）。
    cors_origin_regex: str = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"

    # ---- 冷层（本地 SQLite）----
    # 数据库文件位置（相对 backend 运行目录；默认 backend/data/memory.db）
    cold_db_path: str = "data/memory.db"

    # ---- 会话存储（多轮对话历史）----
    # sqlite：落 backend/data（默认）——刷新页面、重启后端后的历史都在；
    # memory：进程内（测试用，重启即清空）
    session_backend: str = "sqlite"
    # 空 = 跟随冷层库文件（cold_db_path），同库不同表
    session_db_path: str = ""
    # 历史接口一次最多返回的消息条数（界面「历史记录」用；
    # 与喂给模型的窗口 max_history_turns 是两回事）
    session_history_limit: int = 200

    # 每轮「注入了什么」的留痕（JSONL）：排查「记忆串味」时的实证来源。
    # 默认开启——这类问题事后无法复现，没有痕迹就只能猜；写失败不影响对话。
    memory_trace_path: str = "data/logs/memory-trace.jsonl"

    # ---- 记忆召回参数（分层聚合）----
    memory_fact_limit: int = 5      # 语义记忆（事实）召回总条数上限
    memory_fact_anchor_n: int = 3   # 其中：高 importance 锚点保底条数
    memory_fact_relevant_n: int = 2 # 其中：按 query 相关性补充条数
    memory_top_k: int = 3           # 情景记忆（温层）召回条数
    # 各记忆层共用的层内 token 预算（PromptManager 按层分别应用，
    # 而非整个记忆块的总额度）
    memory_layer_budget: int = 400
    # 时间衰减：权重 = 0.5 ** (年龄天数 / 半衰期天数)；指数控制衰减强度
    # （0=不衰减，1=标准半衰期，>1 更强地让位于近期记忆）
    memory_half_life_days: float = 30.0
    memory_decay_exponent: float = 1.0
    # 同情绪记忆的召回分加权系数（参照⑤）
    memory_emotion_boost: float = 1.25
    # 情景记忆淘汰 TTL（天）：超过该时长未被**召回**的记忆会被删除。
    # ⚠️ 必须远大于 memory_half_life_days：衰减会压低旧记忆分数、使其难以
    # 进入 top_k，若不留出重新召回的窗口，会形成「衰减 → 不被召回 → 被删除」
    # 的正反馈，把所有旧记忆清空。
    memory_recall_ttl_days: float = 180.0
    # ---- 混合检索（BM25 稀疏 + 稠密向量 → RRF 融合）----
    memory_hybrid_enabled: bool = True   # 关闭则退化为纯向量召回
    memory_candidate_n: int = 10         # 各通道候选条数（应远大于 memory_top_k）
    memory_rrf_k: int = 60               # RRF 平滑常数（原论文推荐值）
    # 稠密通道相似度阈值：在**融合前**过滤（RRF 分数无绝对含义，无法事后设阈）
    # 注意：依 embedding 模型而异，需实测校准（参考世界书 vector_threshold 的做法）
    memory_min_similarity: float = 0.0
    memory_extractor: str = "rule"  # 回复后抽取器：rule（零依赖）| llm（覆盖率更高，失败自动降级）

    # ---- PromptManager 预算（分层组装）----
    worldbook_budget: int = 400       # 世界书注入块预算
    prompt_history_budget: int = 700  # 滚动窗口预算
    prompt_total_budget: int = 4000   # 提示词总量预算（超出按优先级裁剪）
    prompt_style_budget: int = 400    # 表达风格块预算（含示例对话）
    reply_char_limit: int = 100       # 单次回复字数上限（<= 0 不限制；像打电话，不像念小说）

    # ---- SillyTavern 预设兼容（见 docs/st-preset-compat.md）----
    # 导入的 ST 预设存放目录（相对 backend 运行目录；backend/data/ 已被 .gitignore 覆盖）。
    # 内容属于**用户本地数据**：不进仓库、不随发行物分发。
    st_presets_dir: str = "data/presets"

    # ---- 创作工坊（用户自建角色卡 / 世界书条目）----
    # 用户自撰内容落 backend/data/studio（backend/data/ 已被 .gitignore 覆盖）。
    # 属**用户本地数据**：不进仓库、不随发行物分发。
    studio_dir: str = "data/studio"

    # ---- 插件体系（设计见 AGENTS.md §9）----
    # 第三方插件目录（每个插件一个子目录 + manifest.json）；内置插件由代码注册。
    # 其父目录同时用作插件自有状态目录：<data_dir>/plugins/<plugin_id>/。
    plugins_dir: str = "data/plugins"

    # 第一方插件目录（入库，随项目分发）：目录 + manifest.json 形态。
    # 与 builtin.py 里“代码内注册”的插件互补——前者适合可拆卸的功能插件，
    # 后者适合平台基础的 provider 族（见 §9.10）。
    builtin_plugins_dir: str = "plugins"

    # 额外的插件来源目录（逗号分隔）：把**写在别处的插件**直接接进来，不必复制到
    # `plugins_dir` 下。开发插件时改完代码点一次「重新扫描」即可生效，宿主不碰源文件。
    # 每项都是一个「插件目录的父目录」（其下每个子目录 = 一个插件，含 manifest.json）。
    # 绝对路径原样使用；相对路径按 data_root() 解析。接口见 docs/plugin-development.md。
    plugin_extra_dirs: str = ""

    # 酒馆会话导入记录（幂等去重：只记“哪些会话已导入哪个陪伴对象”）
    tavern_import_state_file: str = "data/tavern_import.json"

    # 旧版「酒馆世界书直接注入」开关，仅为旧 .env / 运行时配置兼容保留。
    # 当前酒馆世界书统一走知识库同步 + 向量/BM25 检索，代码不再读取此开关来构造
    # `WorldBookEntry` 直注入层；新的勾选状态由 tavern-bridge 的 `tavern_disabled_books` 管理。
    tavern_worldbook_enabled: bool = False

    # 启动时把酒馆里**新增**的对话增量同步进记忆（默认关）。
    # 同步按轮次游标做增量：已导过的不重写，接着聊的会补上；
    # 归属按角色分开（选哪个角色只召回那个角色的上下文）。
    # 放后台线程执行——事实抽取要调模型，不能拖住启动。
    tavern_sync_on_startup: bool = False

    # ---- Skill 体系（设计见 AGENTS.md §9.6，独立于插件）----
    # 渐进式加载的能力说明：常驻上下文只有清单（id/名称/适用场景），
    # 正文由 study_skill 工具按需取回。与插件的区别是「提示词级 vs 进程级」。
    skills_enabled: bool = True
    # 内置技能目录（入库，随项目分发）：每技能一个 <id>/SKILL.md
    skills_dir: str = "skills"
    # 用户技能目录（自己写的技能放这里；data/ 已 gitignore）
    user_skills_dir: str = "data/skills"
    # 禁用列表落盘位置（运行期启停覆盖下面的初始值）
    skills_state_file: str = "data/skills.json"
    # **初始**禁用列表（逗号分隔）；用户在界面上的启停会落盘并覆盖它
    skills_disabled: str = ""
    # 常驻清单的 token 预算（清单本身必须便宜）
    skills_catalog_budget: int = 400

    # 上传的数字人模型库（Live2D 模型包 / 静态立绘）。
    #
    # 同样落 backend/data/（已被 .gitignore 覆盖）。刻意**不**写到
    # frontend/public/：上传是运行期行为，而 public 属于构建产物（docker 镜像里只读），
    # 且那样会让后端依赖前端目录结构。前端一律通过
    # GET /media/avatar/models/{id}/files/{path} 取文件。
    avatar_models_dir: str = "data/avatar_models"

    # 对话偏好（人设 / 文风 / 提示词预设 / 酒馆预设）的落盘路径。
    #
    # 为什么放后端而不是 localStorage：桌面端与 Web 端的 origin 不同
    # （控制台/桌宠窗是 127.0.0.1:34567，Web 端是 localhost:3000），
    # localStorage 天然不共享；而「用哪套提示词」必须是三处一致的事实。
    # 语义：里面存的是**用户最后一次的选择**（空串 = 没选过，回落到部署默认）。
    chat_preferences_path: str = "data/chat-preferences.json"

    # 数字人（魔珐）凭证的落盘路径，按形态分组：web（横屏）/ pet（竖屏）。
    #
    # 为什么放后端：与对话偏好同一个理由——三个界面的 origin 不同
    # （控制台/桌宠窗是 127.0.0.1:34567，Web 端是 localhost:3000），
    # localStorage 天然不共享，而「用哪套密钥」必须是三处一致的事实。
    #
    # 为什么要分两套：横屏/竖屏是**魔珐控制台创建应用时**定下的，容器比例必须与
    # 应用类型一致（实测同一个 appId 换比例会变形）。Web 端用横屏应用、桌宠窗用
    # 竖屏应用，所以两个形态各存一份。
    avatar_credentials_path: str = "data/avatar-credentials.json"

    # 数字人模型清单（可选）：本地 JSON 文件路径（也接受 file:// URL）。
    #
    # 用途：把「还有哪些模型可以获取」以清单形式展示给用户（作者 / 授权 / 获取地址）。
    # **只读清单，不代下载**——模型不可自由分发，见 docs/license-compliance.md。
    # 只支持本地路径：拉远程 JSON 是网络访问，而插件权限模型目前只对文件系统
    # 有强制校验，声明 network.hosts 为空却真去发请求等于把声明写成空话。
    # 格式见 backend/plugins/live2d-model-source/model-manifest.example.json。
    live2d_manifest_path: str = ""

    # ---- 文风预设（M5 风格系统）----
    # 与人设正交：人设管「是谁」，文风管「怎么说话」
    style_preset: str = "modern-conversational"

    # ---- 叙事框架层（jailbreak，术语对齐 ST 的 jailbreak 槽位）----
    #
    # **默认关闭**，且这是刻意的产品口径而不是待办：本层会改写模型的回应框架
    # （抑制「作为 AI 我建议你…」这类跳出角色的话、在虚构语境内放开题材），
    # 属于「用户知情后自行开启」的能力，不该是默认体验。
    #
    # 关闭时 PromptManager 的该层为空串，system 提示与不装本层时**逐字一致**
    # （有回归测试守着，见 backend/tests/test_rag/test_prompt_manager_jailbreak.py）。
    #
    # 预设内容与代码分离，落在 app/prompts/jailbreak/presets/*.yaml；
    # 本层与 persona / style 正交，可任意组合。
    jailbreak_enabled: bool = False
    # 生效的框架预设 id；空串 = 启用时回落到内置默认档（immersive-narrative）
    jailbreak_preset: str = ""
    # 框架块 token 预算（内置预设 550~900 字，600 足够；不够时整块被裁并记 warning）
    jailbreak_budget: int = 600

    # ---- 温层（向量库：memory 本地假实现 | qdrant）----
    # 评审用本地 docker：http://qdrant:6333（容器内互联）；开发用云 URL
    warm_backend: str = "memory"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # ---- 中文分词（BM25 稀疏检索通道）----
    # auto（默认，jieba 可用则用，否则降级字符 bigram）| jieba（强制，未装则报错）
    # | char-bigram（零依赖，CI / 离线演示）
    # 拆分与工厂见 app/rag/retrieval/tokenize/（AGENTS.md §9.11 P2）
    tokenizer_backend: str = "auto"

    # ---- LLM 提供商（评审自填：dashscope | siliconflow | openai-compatible | mock）----
    llm_provider: str = "mock"  # 默认 mock：无 key 也可跑通对话链路（占位回复）
    llm_api_key: str = ""
    llm_model: str = "qwen2.5-7b-instruct"
    llm_base_url: str = ""  # openai-compatible 时必填，如 https://api.example.com/v1
    llm_timeout: float = 120.0  # 单次请求超时（秒）；prompt 较长或生成较长时需放宽
    # 推理模型开关：qwen3 等推理模型默认会先生成大量思考 token（实测慢 4-5 倍），
    # 情感陪伴场景不需要长思考，默认关闭（None = 不传该参数，兼容非推理模型）
    llm_enable_thinking: bool = False

    # ---- 情绪兜底策略（主通道是 LLM function calling，固定不可换）----
    # regex（默认，关键词/正则）| neutral（不判断，恒中性）
    # 选 neutral 的场合：正则读不懂否定（「我不开心」会命中 HAPPY 的「开心」），
    # 而情绪会驱动 3D 表情与记忆加权——宁可中性也不要判反。见 app/tools/emotion/。
    emotion_fallback: str = "regex"

    # ---- 个人记忆（知识库：用户上传的私人语料）----
    knowledge_enabled: bool = True
    # 存储后端：留空则**跟随温层**（memory | qdrant）——评审只需配一处
    knowledge_backend: str = ""
    knowledge_max_file_mb: float = 5.0        # 单文件大小上限
    # 检索注入：条数 / 各通道候选数 / RRF 常数 / 注入 prompt 的 token 预算
    knowledge_top_k: int = 3
    knowledge_candidate_n: int = 10
    knowledge_rrf_k: int = 60
    knowledge_context_budget: int = 900
    # 分块参数（字符）：target 聚合目标 / max 单块硬上限 / overlap 句级回退
    knowledge_chunk_target: int = 400
    knowledge_chunk_max: int = 600
    knowledge_chunk_overlap: int = 80
    # 语义边界判据（关闭则退化为纯递归字符分块）
    knowledge_semantic_chunking: bool = True
    knowledge_semantic_threshold: float = 0.75
    # MinHash 近似判重阈值（命中不自动覆盖，需 force=true）
    knowledge_dedup_threshold: float = 0.85

    # ---- Embedding（deterministic 本地实现 | dashscope | siliconflow | openai-compatible）----
    embedding_provider: str = "deterministic"
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v3"
    embedding_base_url: str = ""
    # 向量维度：必须与模型实际输出一致（Qdrant 建 collection 需要静态维度，
    # 不能等首次请求才确定）。常见值：text-embedding-v3 = 1024，
    # BAAI/bge-m3 = 1024，Qwen/Qwen3-Embedding-8B = 4096。
    # 配置不符时 provider 会在首次请求报错并告知实际维度。
    embedding_dim: int = 1024
    embedding_timeout: float = 30.0
    # 编码结果 LRU 缓存条数：一轮对话会对同一句输入重复编码 4–7 次
    # （世界书 / 知识库各作用域 / 温层），缓存把它们压成 1 次远程请求。
    # 单条 4096 维向量在 Python 里约 130KB，64 条 ≈ 8MB。
    # 构造期读取，改完需重建对话图（或重启后端）。
    embedding_cache_size: int = 64

    # ---- Agent 行动层（P1）----
    agent_tools_enabled: bool = True   # 是否启用工具调用（情绪日记/趋势/呼吸引导/记忆检索）
    max_tool_rounds: int = 2           # 工具调用轮数上限（防死循环）

    # =============================================================
    # 感知层（多模态输入）—— 设计见 docs/proactive-multimodal.md §4
    # =============================================================
    #
    # 总口径：**感知默认全关、逐项显式开启**。与插件「默认启用」刻意不同——
    # 插件是功能，感知是隐私。

    # ---- 语音识别（ASR，§4.3）----
    # 这个字段**本身就是开关**（与 DIGITAL_HUMAN_PROVIDER 同一口径）：
    # 再设一个 ASR_ENABLED 会出现「开关开着但 provider=none」这种自相矛盾的状态，
    # 两个配置项迟早互相打架。
    #   none            = 关闭（默认）。界面不渲染麦克风
    #   faster-whisper  = 本地识别（需 pip install faster-whisper）
    asr_provider: str = "none"
    # 模型规格：tiny / base / small / medium / large-v3。越大越准也越慢。
    # small 是「中文日常对话 + CPU 可用」的平衡点。
    asr_model: str = "small"
    # 模型权重目录（相对路径按数据目录解析）。
    # ⚠️ **模型不进发布包**（AGENTS.md §3.2 的同一条原则）：发布物里没有它，
    # 由用户自备。该目录下应有 <规格>/model.bin 等文件。
    asr_model_dir: str = "models/whisper"
    # 允许 faster-whisper 自己下载权重时的落点。**默认空 = 不自动下载**——
    # 静默拉 1GB 权重既慢又常失败（国内网络），且用户不知道机器上在发生什么。
    # 填了才让它下载（例如填与 asr_model_dir 相同的值）。
    asr_download_root: str = ""
    asr_device: str = "cpu"            # cpu | cuda
    asr_compute_type: str = "int8"     # CPU 用 int8；CUDA 可换 float16
    asr_beam_size: int = 5
    asr_language: str = "zh"           # 空串 = 自动检测
    # 用 Silero VAD 先切掉非语音段。**默认开**，而且这不是调优是正确性：
    # Whisper 在没人声的音频上会吐出训练集水印（实测 small 模型 + 纯音 →
    # 「字幕by索兰娅」），而那会被当成用户说的话填进输入框。
    asr_vad_filter: bool = True
    # 无语音判定阈值（越大越容易判成「没人说话」）。与 VAD 一起构成两道防线，
    # 漏网的由 provider 里的文本过滤兜住。
    asr_no_speech_threshold: float = 0.6
    # 是否在对话界面显示语音按钮。关掉后**能力仍在**（接口照常可用），
    # 只是不再渲染入口——给「用快捷键说话、不想看见按钮」的人留的开关。
    asr_ui_enabled: bool = True
    # 语音快捷键（形如 `Ctrl+Shift+M`；空串 = 不启用）。
    # 按下开始录音、再按一次结束，等价于点按钮。
    # 只在窗口获得焦点时生效——不注册系统级全局热键：那需要主进程介入，
    # 且会与用户的其它软件抢键（一个「陪伴」应用抢全局热键是很不礼貌的）。
    asr_shortcut: str = ""
    # 单次上传音频的大小上限（MB）。语音输入是「说一句话」，不是传录音文件。
    asr_max_file_mb: float = 25.0
    # 启动时后台预热一次（加载模型，让第一次说话不必等十几秒）；失败只记日志
    asr_warmup: bool = True

    # ---- 图片理解（视觉，§4.4）—— 已决策：Qwen2.5-VL ----
    #
    # **只做「用户主动分享」**：摄像头与连续屏幕感知已明确否决
    # （隐私成本远高于演示收益）。因此这里没有「采集频率」这类配置——
    # 结构上就不存在后台采集。
    #
    #   none               = 关闭（默认）。界面不渲染「发图」入口
    #   openai-compatible  = Qwen2.5-VL（走 OpenAI 兼容端点，零新依赖）
    #   dashscope / siliconflow = 同上，并自动取该家的默认端点
    vision_provider: str = "none"
    vision_api_key: str = ""
    vision_model: str = "qwen2.5-vl-72b-instruct"
    # 留空 = 按 provider 取默认端点（dashscope / siliconflow 均有内置默认值）
    vision_base_url: str = ""
    vision_timeout: float = 60.0
    # 单张图片大小上限（MB）。**图片以 data URL 内联进请求、不落盘**（§4.6）
    vision_max_image_mb: float = 8.0
    # 描述指令（留空 = 用内置的「只描述看得见的」客观描述指令）。
    # 预设内容与代码分离的口径与 prompts/ 一致：文案可改，机制不动。
    vision_prompt: str = ""
    # 图片事实的存活秒数。比其它感知事实长（默认 30 分钟）：
    # 分享一张图是「这一段时间的上下文」，不像歌名那样转瞬即逝。
    vision_ttl_seconds: int = 1800
    # 是否在对话界面显示「图片」按钮（关掉后接口仍可用，只是不渲染入口）
    vision_ui_enabled: bool = True

    # ---- 桌面情景（§4.2 / §4.4）----
    # 桌宠端（Electron）是否采集并上报桌面情景。Web 端没有这个能力，
    # 与开关无关——它拿不到 SMTC / 前台窗口。
    perception_desktop_enabled: bool = True
    # 是否把**前台窗口标题**注入提示词。默认 False：
    # 歌名是用户主动播放的、可以聊；窗口标题可能是一封邮件或一个病历页面。
    # （窗口的**进程名**始终会采集，它是「忙碌判定」必需的，但不进提示词。）
    perception_include_window_title: bool = False
    # 感知事实的默认存活秒数（超过即从快照里消失，见 app/perception/snapshot.py）
    perception_ttl_seconds: int = 300
    # 感知层（「此刻」）的 token 预算。行踪与画像进来之后，默认值从 120 上调到
    # 200——那两行是主动开口「有由头」的来源，被裁掉就退化成空泛问候了。
    perception_budget: int = 200

    # ---- 时间感（§4.8）----
    # 是否把「现在几点 / 星期几 / 什么时段」告诉角色。**默认开**：
    # 这是对话里最基础的一条上下文，而它原先只在桌面情景上报里才有
    # （Web 端完全没有、桌宠窗关着也没有）。见 app/perception/clock.py。
    perception_include_time: bool = True

    # ---- 行踪（活动轨迹，§4.9）----
    # 是否记录并注入「主人最近在电脑上干什么」。**默认开**，且默认只记进程名。
    # 关掉后采集链路立即停止，已有轨迹也会被清掉（§4.6 的红线）。
    perception_activity_enabled: bool = True
    # 「最近在做什么」的回看窗口（小时）。3 小时：够覆盖「这一段在忙什么」，
    # 又不会把上午的事说成「最近」。
    perception_activity_recent_hours: float = 3.0
    # 轨迹保留天数。**刻意短**：行踪是敏感数据，够用就好。
    perception_activity_retention_days: int = 7
    # 轨迹里是否连**窗口标题**一起记。默认 False——标题是内容
    # （可能是一封邮件、一个病历页面），进程名才是类别。
    perception_activity_include_title: bool = False
    # 空闲多少秒算「人不在」并断开当前这一段。300s：接个电话不会断开，
    # 真的离开一定会断开。不断开的话会记出「连续 8 小时在用 VS Code」。
    perception_activity_idle_gap_seconds: float = 300.0
    # 同一进程最长连续算一段的时间（小时）。超过就断开，
    # 让「上午 / 下午」在行踪里可分辨。<=0 表示不断开。
    perception_activity_max_segment_hours: float = 4.0

    # ---- 偏好画像（§4.9）----
    # 是否注入「最近几天最常用什么、什么时候活跃」的聚合画像。
    # 刻意是**确定性计数**而非模型总结（可解释 / 零成本 / 不越界，见 profile.py）。
    perception_profile_enabled: bool = True
    # 画像回看天数
    perception_profile_days: int = 7
    # 产出画像所需的最小累计时长（分钟）。低于它不出画像——
    # 用两分钟的行踪说「他最常用 Code.exe」是不诚实的。
    perception_profile_min_minutes: float = 30.0

    # =============================================================
    # 主动链路（会自己开口）—— 设计见 docs/proactive-multimodal.md §5
    # =============================================================
    #
    # 已决策：**默认开**。但这个「开」的完整含义是——
    # 下面 2~7 道闸门（免打扰 / 冷却 / 配额 / 对话中不插话）**同时默认生效**。
    # 用户什么都不设，体验已经是收敛的。
    #
    # 频率默认值在实现后**上调过一轮**（冷却 90→30 分钟、配额 6→12、
    # 静默 6→3 小时）：原值是按「宁可少说」定的，实际用下来
    # 一天 6 条、每条间隔一个半小时，陪伴感偏弱——用户要的是「会主动找我」，
    # 而不是「一天问候一次」。调整的只是**默认值**，闸门机制一字未动，
    # 想要更安静的用户把这几个数改回去即可（见 docs/proactive-multimodal.md §5.3）。
    proactive_enabled: bool = True
    # 调度轮询间隔（秒）。60s 足够——主动开口的时间精度要求是「分钟级」，
    # 更密只会让日志变吵。
    proactive_interval_seconds: float = 60.0
    # 闸门②：免打扰时段（HH:MM-HH:MM，跨零点写成 23:00-08:00）
    proactive_quiet_hours: str = "23:00-08:00"
    # 闸门②豁免：**关怀型**触发（如「凌晨还在活动」）是否可越过免打扰。
    # 这条例外的意义：深夜是情感陪伴最需要它的时候——
    # 免打扰的目的是「别吵醒睡着的人」，不是「看着你熬夜也不说话」。
    proactive_quiet_exempt_care: bool = True
    # 闸门③：两条主动消息的最小间隔（分钟）。
    # 30 分钟仍能保证「不会连着两句」；再短就该由闸门⑤（对话中不插话）兜底了。
    proactive_min_interval_minutes: int = 30
    # 闸门④：每日主动消息上限。12 条 ≈ 清醒时段每 1~1.5 小时一次，
    # 这是「活跃但不烦人」的档位；真正防轰炸的是闸门③与闸门⑤，不是这个上限。
    proactive_daily_quota: int = 12
    # 闸门⑤：用户刚刚说过话（< N 分钟）时不主动——此时该**回应**，不该另起话头
    proactive_conversation_window_minutes: int = 2
    # 定时触发器：每天这个时刻开口（空串 = 关闭该触发器）
    proactive_daily_time: str = "09:00"
    # 间隔触发器：用户静默超过这么多小时就主动问一句（<=0 = 关闭该触发器）
    proactive_idle_hours: float = 3.0
    # 记忆到期触发器：**进行中**的事项搁置超过这么多天就回头问一句
    # （「你上次说在准备面试，后来怎么样了？」）。<=0 = 关闭该触发器。
    # 同一件事只问一次（问两遍是唠叨），所以这个值只影响「多久后问」。
    proactive_memory_followup_days: float = 3.0
    # 行踪触发器①「久别回来」：用户离开超过这么多分钟后重新回到电脑前，
    # 可以自然地打个招呼（「回来啦」）。<=0 = 关闭该触发器。
    # 30 分钟：够区分「去倒杯水」与「出门了一趟」。
    proactive_back_from_away_minutes: float = 30.0
    # 行踪触发器②「忙完了」：从工作类应用（写代码 / 文档 / 设计）切到
    # 娱乐类（游戏 / 看片），且新的一段已经持续这么多分钟，可以问一句。
    # <=0 = 关闭该触发器。20 分钟：短于它可能只是切过去看一眼。
    proactive_activity_shift_minutes: float = 20.0
    # 主动开口的字数上限（比 REPLY_CHAR_LIMIT 更短：搭话不该是一段独白）
    proactive_reply_char_limit: int = 60
    # 节流状态落盘位置（原子写；文件损坏按「从未主动开口」处理）
    proactive_state_path: str = "data/proactive-state.json"
    # 主动开口面向哪个陪伴对象。**空串 = 用当前偏好里的角色**
    # （与启动期酒馆同步同一口径：非请求上下文只能读用户偏好）
    proactive_persona_id: str = ""

    # ---- MCP（把外部 MCP server 的工具接入 Agent 行动层）----
    # 清单默认取 backend/mcp_servers.json；文件不存在 = 未配置（等同不启用）
    mcp_enabled: bool = True
    mcp_servers_file: str = ""         # 空 = 用默认清单路径
    mcp_connect_timeout: float = 20.0  # 单台服务器连接超时（秒）
    mcp_call_timeout: float = 30.0     # 单次 MCP 工具调用超时（秒）

    # ---- 数字人驱动（M5）----
    digital_human_provider: str = "local"   # local（零依赖降级）| xmov（魔珐星云）| gpt_sovits（自部署 TTS）
    xmov_app_id: str = ""                    # 魔珐控制台「密钥管理」获取
    xmov_secret: str = ""
    xmov_voice: str = "XMOV_LV_TTS__13"      # 基础音色；Pro 音色另计费
    xmov_host: str = "nebula-agent.xingyun3d.com"
    media_dir: str = "media"                 # 音频/视频产物目录（gitignore）
    avatar_enabled: bool = True              # 是否在 chat 后附带数字人驱动数据

    # ---- GPT-SoVITS（自部署 TTS：给 Live2D / 静态立绘出真声音）----
    #
    # 开关就是上面的 digital_human_provider=gpt_sovits：**不再设第二个 TTS 开关**，
    # 否则两个配置项会互相打架（谁生效？）。
    # 服务未部署时无需改动配置：驱动自动降级为无音频，前端保持静默（只显示文字）。
    gpt_sovits_base_url: str = "http://127.0.0.1:9880"
    # 参考音频路径：GPT-SoVITS 零样本克隆里「音色」= 参考音频 + 它对应的文字。
    # 填**跑 GPT-SoVITS 那台机器**上可见的路径（不是本机相对路径）。
    gpt_sovits_ref_audio: str = ""
    gpt_sovits_prompt_text: str = ""
    gpt_sovits_prompt_lang: str = "zh"
    gpt_sovits_text_lang: str = "zh"
    gpt_sovits_speed: float = 1.0
    # 输出容器：wav（推荐，浏览器 <audio> 可直接播放）| ogg | aac | raw
    # raw 是裸 PCM，放不了，别选。
    gpt_sovits_media_type: str = "wav"
    gpt_sovits_timeout: float = 60.0
    # 额外请求参数（一行 JSON，原样透传给 GPT-SoVITS 的 /tts，用于按需调优）。
    # 实测可用的例子：{"parallel_infer": false}（略快）。留空 = 全用服务端默认值。
    # ⚠️ 核心字段（text / ref_audio_path / media_type 等）不允许被覆盖，会被忽略并记 warning。
    gpt_sovits_extra_params: str = ""
    # 启动时后台预热一次（让模型/显存预热，首个请求不偏慢）；失败只记日志
    gpt_sovits_warmup: bool = True
    # 音色表（JSON：{音色id: {ref_audio_path, prompt_text, prompt_lang, label}}）；
    # 文件不存在 = 只用默认音色。属**用户本地数据**（backend/data/ 已被 gitignore）。
    gpt_sovits_voices_file: str = "data/tts_voices.json"
    gpt_sovits_default_voice: str = ""      # 空 = 用 gpt_sovits_ref_audio 作默认音色

    @model_validator(mode="after")
    def _resolve_path_fields(self) -> "Settings":
        """先应用运行时覆盖（界面设置），再把路径字段解析成绝对路径。

        为什么集中在校验期做：用这批字段的消费点有 15 处以上
        （`Path(settings.media_dir)` 之类），逐个改造既啰嗦又容易漏；
        在这里统一解析后它们拿到的就是绝对路径，行为与改造前一致
        （改造前依赖 cwd 恰好是 backend/）。

        **覆盖必须排在路径解析之前**：用户可能把 `gpt_sovits_voices_file` 这类
        路径字段填成相对路径，先覆盖再解析才能按 data_root() 正确落到数据目录
        （顺序反了会把它当成绝对路径放过）。

        空串一律保持空串：多处用「空 = 未配置 / 跟随上一层」表达分支
        （`session_db_path` 跟随冷层库文件、`mcp_servers_file` 用默认清单）。
        """
        for field, value in _runtime_overrides.items():
            setattr(self, field, value)

        for field in _DATA_PATH_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, resolve_under(data_root(), value))

        for field in _RESOURCE_PATH_FIELDS:
            value = getattr(self, field)
            if value:
                setattr(self, field, resolve_under(resource_root(), value))

        return self


def get_settings() -> Settings:
    """返回单例配置（FastAPI 依赖注入用）。"""
    return Settings()


def cors_origin_list(settings: Settings) -> list[str]:
    """把逗号分隔的 CORS 配置解析为列表（支持 "*" 通配）。"""
    raw = (settings.cors_origins or "").strip()
    if raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def skills_disabled_list(settings: Settings) -> list[str]:
    """把逗号分隔的 SKILLS_DISABLED 解析为列表（技能库的初始禁用值）。"""
    raw = (settings.skills_disabled or "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def plugin_extra_dirs_list(settings: Settings) -> list[str]:
    """把逗号分隔的 `PLUGIN_EXTRA_DIRS` 解析为绝对路径列表。

    绝对路径原样使用（用户把插件放在自己的仓库 / 编辑器工作区里）；相对路径按
    `data_root()` 解析——与 `plugins_dir` 同一口径，免得同一份配置在两台机器上
    落到不同的地方。空项跳过（末尾多一个逗号不该被当成当前目录）。
    """
    raw = (settings.plugin_extra_dirs or "").strip()
    out: list[str] = []
    for item in raw.split(","):
        value = item.strip()
        if value:
            out.append(resolve_under(data_root(), value))
    return out


def plugin_search_dirs(settings: Settings) -> list[str]:
    """插件扫描目录，**顺序即优先级**（`PluginManager.discover` 里「先注册者胜出」）。

    第一方插件在最前：同 id 时内置实现优先于用户目录里的同名插件（§9.3）。
    """
    return [
        settings.builtin_plugins_dir,
        settings.plugins_dir,
        *plugin_extra_dirs_list(settings),
    ]

