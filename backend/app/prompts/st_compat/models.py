"""SillyTavern 预设数据模型（P1 解析层）。

事实来源（见 docs/st-preset-compat.md 末尾的来源索引）：
- 条目字段与常量来自 ST `public/scripts/PromptManager.js`（Prompt 类、
  `DEFAULT_DEPTH = 4`、`DEFAULT_ORDER = 100`、`INJECTION_POSITION`）；
- 默认 marker 清单来自 ST `default/content/presets/openai/Default.json`。

两条设计约束：
1. **容错优先**：ST 预设字段随版本演进、社区预设还常带私有字段，故一律
   `extra="allow"`——未知字段原样保留，导出时可原样带回；
2. **合规**：`content` / `name` 属于**用户本地数据**（用户自己导入的预设文件），
   本项目只做解析与渲染，不在仓库或发行物中内置任何 ST / 社区的提示词原文。

注意：模型只保证**类型**与**缺省值**；"非法值回退 + 记警告"由 parser 负责，
因为模型层没有告警通道（见 parser.py）。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---- ST 常量（缺省值，字段缺失时对齐 ST 行为）----
DEFAULT_DEPTH = 4
DEFAULT_ORDER = 100

# ---- 注入位置（对齐 ST 的 INJECTION_POSITION 枚举）----
POSITION_RELATIVE = 0   # 跟着 prompt_order 的顺序走
POSITION_ABSOLUTE = 1   # In-Chat：按 depth 插进对话历史

# ---- 消息角色 ----
VALID_ROLES = ("system", "user", "assistant")

#: 角色别名：社区预设里常见非标准写法（Gemini 风格的 `model`、旧版的 `ai`），
#: 语义上等于 assistant —— 直接回退 system 会让这些条目的效果彻底错位。
ROLE_ALIASES: dict[str, str] = {
    "model": "assistant",
    "ai": "assistant",
    "bot": "assistant",
    "char": "assistant",
    "character": "assistant",
    "human": "user",
}


def normalize_role(role: str) -> str:
    """把角色名归一到 `VALID_ROLES` 之一（未知值回退 system）。"""
    value = (role or "").strip().lower()
    if value in VALID_ROLES:
        return value
    return ROLE_ALIASES.get(value, "system")


def is_known_role_alias(role: str) -> bool:
    """是否为已知别名（已知别名不产生“不受支持”告警，避免噪声）。"""
    return (role or "").strip().lower() in ROLE_ALIASES

# ---- marker 标识（正文由 HyPRA 运行时填充，映射见契约文档 §5）----
MARKER_MAIN = "main"
MARKER_WORLD_INFO_BEFORE = "worldInfoBefore"
MARKER_WORLD_INFO_AFTER = "worldInfoAfter"
MARKER_CHAR_DESCRIPTION = "charDescription"
MARKER_CHAR_PERSONALITY = "charPersonality"
MARKER_SCENARIO = "scenario"
MARKER_PERSONA_DESCRIPTION = "personaDescription"
MARKER_DIALOGUE_EXAMPLES = "dialogueExamples"
MARKER_CHAT_HISTORY = "chatHistory"
MARKER_JAILBREAK = "jailbreak"
MARKER_NSFW = "nsfw"
MARKER_ENHANCE_DEFINITIONS = "enhanceDefinitions"

MARKER_IDENTIFIERS = frozenset(
    {
        MARKER_MAIN,
        MARKER_WORLD_INFO_BEFORE,
        MARKER_WORLD_INFO_AFTER,
        MARKER_CHAR_DESCRIPTION,
        MARKER_CHAR_PERSONALITY,
        MARKER_SCENARIO,
        MARKER_PERSONA_DESCRIPTION,
        MARKER_DIALOGUE_EXAMPLES,
        MARKER_CHAT_HISTORY,
        MARKER_JAILBREAK,
        MARKER_NSFW,
        MARKER_ENHANCE_DEFINITIONS,
    }
)

# ---- 生成类型触发（injection_trigger）----
TRIGGER_SUPPORTED = frozenset({"normal", "regenerate"})
TRIGGER_UNSUPPORTED = frozenset({"continue", "impersonate", "swipe", "quiet"})

# ---- 端点与密钥类字段：忽略且**不落盘**（避免误存用户凭证）----
# 依据 ST Default.json 的 provider 相关字段清单；另按后缀兜底匹配。
_SENSITIVE_KEYS_EXACT = frozenset(
    {
        "chat_completion_source",
        "openai_model",
        "claude_model",
        "openrouter_model",
        "openrouter_use_fallback",
        "openrouter_group_models",
        "openrouter_sort_models",
        "openrouter_providers",
        "openrouter_quantizations",
        "ai21_model",
        "mistralai_model",
        "chutes_model",
        "chutes_sort_models",
        "minimax_model",
        "minimax_endpoint",
        "electronhub_model",
        "electronhub_sort_models",
        "electronhub_group_models",
        "custom_model",
        "custom_url",
        "custom_include_body",
        "custom_exclude_body",
        "custom_include_headers",
        "google_model",
        "vertexai_model",
        "reverse_proxy",
        "proxy_password",
    }
)

_SENSITIVE_SUFFIXES = ("_api_key", "_apikey", "_token", "_secret", "_password")


def is_sensitive_key(key: str) -> bool:
    """判断顶层字段是否属于「端点/密钥类」（导入时剥离、不落盘）。"""
    lowered = key.lower()
    return lowered in _SENSITIVE_KEYS_EXACT or lowered.endswith(_SENSITIVE_SUFFIXES)


class STPromptItem(BaseModel):
    """ST 预设里的一个提示词条目（`prompts[]` 单项）。"""

    model_config = ConfigDict(extra="allow")

    identifier: str = ""
    name: str = ""
    content: str = ""
    role: str = "system"
    system_prompt: bool = False
    marker: bool = False
    injection_position: int = POSITION_RELATIVE
    injection_depth: int = DEFAULT_DEPTH
    injection_order: int = DEFAULT_ORDER
    injection_trigger: list[str] = Field(default_factory=list)
    forbid_overrides: bool = False
    extension: bool = False

    # None 视为"未提供"（ST 有的预设会显式写 null）
    @field_validator(
        "identifier", "name", "content", "role", mode="before"
    )
    @classmethod
    def _none_to_empty(cls, value: object) -> object:
        return "" if value is None else value

    @field_validator("injection_trigger", mode="before")
    @classmethod
    def _none_to_empty_list(cls, value: object) -> object:
        return [] if value is None else value

    @property
    def is_marker(self) -> bool:
        """是否为占位条目（正文由运行时填充）。"""
        return bool(self.marker)

    @property
    def is_in_chat(self) -> bool:
        """是否为 In-Chat 注入（按 depth 插进历史）。"""
        return self.injection_position == POSITION_ABSOLUTE

    @property
    def is_supported_trigger(self) -> bool:
        """生成类型触发是否全部受支持（空 = 全部类型，天然受支持）。"""
        return all(t in TRIGGER_SUPPORTED for t in self.injection_trigger)

    @property
    def display_name(self) -> str:
        """界面展示名（缺省回落到 identifier）。"""
        return self.name or self.identifier


class STPromptOrderEntry(BaseModel):
    """`prompt_order[].order[]` 单项：某个条目是否启用。"""

    model_config = ConfigDict(extra="allow")

    identifier: str
    enabled: bool = True


class STPromptOrder(BaseModel):
    """一份顺序表（ST 按 character_id 分别保存）。"""

    model_config = ConfigDict(extra="allow")

    character_id: int | str = 0
    order: list[STPromptOrderEntry] = Field(default_factory=list)

    @field_validator("order", mode="before")
    @classmethod
    def _none_to_empty_list(cls, value: object) -> object:
        return [] if value is None else value


class STPreset(BaseModel):
    """一份 ST Chat Completion 预设。

    采样参数用 `None` 表示"该预设未提供此项"——合并时跳过，不覆盖上层来源。
    组装控制字段的默认值取**中性值**（空字符串 / False），而不是 ST 的默认文案：
    未提供即不注入，避免把 ST 的默认模板文本带进本项目。
    """

    model_config = ConfigDict(extra="allow")

    # ---- 采样参数 ----
    temperature: float | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    top_k: float | None = None
    top_a: float | None = None
    min_p: float | None = None
    repetition_penalty: float | None = None
    seed: int | None = None
    n: int | None = None
    openai_max_tokens: int | None = None
    openai_max_context: int | None = None

    # ---- 组装控制 ----
    use_sysprompt: bool = False             # System Prompt 覆盖开关（契约文档 §6.4）
    squash_system_messages: bool = False    # 相邻 system 消息合并
    names_behavior: int = 0                 # 历史消息是否带「角色名: 」前缀
    wi_format: str = ""                     # 世界书包装模板（空 = 原样注入）
    scenario_format: str = ""
    personality_format: str = ""
    new_chat_prompt: str = ""
    new_example_chat_prompt: str = ""
    continue_nudge_prompt: str = ""
    assistant_prefill: str = ""
    continue_prefill: bool = False
    continue_postfix: str = ""
    send_if_empty: str = ""

    # ---- 条目与顺序 ----
    prompts: list[STPromptItem] = Field(default_factory=list)
    prompt_order: list[STPromptOrder] = Field(default_factory=list)

    @field_validator("prompts", "prompt_order", mode="before")
    @classmethod
    def _none_to_empty_list(cls, value: object) -> object:
        return [] if value is None else value

    @property
    def sampling(self) -> dict[str, float | int]:
        """已提供的采样参数（None 项不出现，便于合并时跳过）。"""
        raw = {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "top_k": self.top_k,
            "top_a": self.top_a,
            "min_p": self.min_p,
            "repetition_penalty": self.repetition_penalty,
            "seed": self.seed,
            "n": self.n,
            "max_tokens": self.openai_max_tokens,
        }
        return {key: value for key, value in raw.items() if value is not None}

    @property
    def context_budget(self) -> int | None:
        """提示词总量预算（对应 ST 的 openai_max_context）。"""
        if self.openai_max_context is None:
            return None
        return int(self.openai_max_context)

    @property
    def enable_thinking(self) -> bool | None:
        """由 ST 的 `show_thoughts` 近似推断本项目的思考开关。

        ST 的 `show_thoughts`（Request model reasoning）= “向模型请求思考过程”，
        与本项目 `enable_thinking` **同向**（都表达“是否让模型思考”）。
        未提供该字段时返回 None（不覆盖内置档的取值）。
        """
        value = (self.model_extra or {}).get("show_thoughts")
        if isinstance(value, bool):
            return value
        return None


def strip_sensitive_keys(data: dict) -> tuple[dict, list[str]]:
    """剥离顶层端点/密钥类字段。

    返回: (剥离后的新字典, 被剥离的字段名列表)。此函数不改动入参。
    字段名会回传供界面提示，但**值一律丢弃**（不落盘）。
    """
    cleaned: dict = {}
    stripped: list[str] = []
    for key, value in data.items():
        if is_sensitive_key(key):
            stripped.append(key)
            continue
        cleaned[key] = value
    return cleaned, stripped
