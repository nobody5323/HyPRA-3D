"""应用配置：从 backend/.env 读取运行参数与云端密钥。

红线：云端密钥（百炼 / 硅基流动 / 魔珐星云 / Qdrant）一律只放
backend/.env（已被 .gitignore 排除），不入库。格式参考 backend/.env.example。

双模式（配置驱动，代码零硬编码）：
- 开发：向量库用云 Qdrant、模型用云 key；
- 评审：docker compose 本地 Qdrant（QDRANT_URL=http://qdrant:6333），
  embedding/LLM 由评审在 .env 自行填入（provider/key/model 均走配置）。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 本文件位于 backend/app/config.py，.env 约定在 backend/.env
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


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

    # 酒馆会话导入记录（幂等去重：只记“哪些会话已导入哪个陪伴对象”）
    tavern_import_state_file: str = "data/tavern_import.json"

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

    # ---- Agent 行动层（P1）----
    agent_tools_enabled: bool = True   # 是否启用工具调用（情绪日记/趋势/呼吸引导/记忆检索）
    max_tool_rounds: int = 2           # 工具调用轮数上限（防死循环）

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
    # 服务未部署时无需改动配置：驱动自动降级为无音频，前端回落浏览器原生 TTS。
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
