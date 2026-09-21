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

    # ---- 冷层（本地 SQLite）----
    # 数据库文件位置（相对 backend 运行目录；默认 backend/data/memory.db）
    cold_db_path: str = "data/memory.db"

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

    # ---- 文风预设（M5 风格系统）----
    # 与人设正交：人设管「是谁」，文风管「怎么说话」
    style_preset: str = "modern-conversational"

    # ---- 温层（向量库：memory 本地假实现 | qdrant）----
    # 评审用本地 docker：http://qdrant:6333（容器内互联）；开发用云 URL
    warm_backend: str = "memory"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # ---- LLM 提供商（评审自填：dashscope | siliconflow | openai-compatible | mock）----
    llm_provider: str = "mock"  # 默认 mock：无 key 也可跑通对话链路（占位回复）
    llm_api_key: str = ""
    llm_model: str = "qwen2.5-7b-instruct"
    llm_base_url: str = ""  # openai-compatible 时必填，如 https://api.example.com/v1
    llm_timeout: float = 120.0  # 单次请求超时（秒）；prompt 较长或生成较长时需放宽
    # 推理模型开关：qwen3 等推理模型默认会先生成大量思考 token（实测慢 4-5 倍），
    # 情感陪伴场景不需要长思考，默认关闭（None = 不传该参数，兼容非推理模型）
    llm_enable_thinking: bool = False

    # ---- 个人记忆（知识库：用户上传的私人语料）----
    knowledge_enabled: bool = True
    # 存储后端：留空则**跟随温层**（memory | qdrant）——评审只需配一处
    knowledge_backend: str = ""
    knowledge_max_file_mb: float = 5.0        # 单文件大小上限
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
    digital_human_provider: str = "local"   # local（零依赖降级）| xmov（魔珐星云）
    xmov_app_id: str = ""                    # 魔珐控制台「密钥管理」获取
    xmov_secret: str = ""
    xmov_voice: str = "XMOV_LV_TTS__13"      # 基础音色；Pro 音色另计费
    xmov_host: str = "nebula-agent.xingyun3d.com"
    media_dir: str = "media"                 # 音频/视频产物目录（gitignore）
    avatar_enabled: bool = True              # 是否在 chat 后附带数字人驱动数据


def get_settings() -> Settings:
    """返回单例配置（FastAPI 依赖注入用）。"""
    return Settings()


def cors_origin_list(settings: Settings) -> list[str]:
    """把逗号分隔的 CORS 配置解析为列表（支持 "*" 通配）。"""
    raw = (settings.cors_origins or "").strip()
    if raw == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]
