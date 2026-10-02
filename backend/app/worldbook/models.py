"""世界书条目数据模型（借鉴 SillyTavern World Info 机制思想，自写实现）。

一个条目 = 触发条件（关键词 / 正则 / 语义向量，任一命中即触发）
        + 注入内容 + 控制参数。
"""

import re

from pydantic import BaseModel, Field, field_validator

#: 归属通配符：该条目对**所有**陪伴对象生效（内置条目的默认值）
SCOPE_ALL = "*"

#: 归属角色 id 允许的字符集（与角色 id、Qdrant collection 命名约束保持一致）
_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

#: 注入档位 —— 与 SillyTavern 的 `world_info_position` 一一对应，
#: 但用**字符串**表达：契约不绑定某一家的枚举数值（见 app/plugins/contracts.py）。
POSITION_BEFORE_CHAR = "before_char"    # ST 0：人设前
POSITION_AFTER_CHAR = "after_char"      # ST 1：人设后
POSITION_BEFORE_AN = "before_an"        # ST 2：作者注（AN）上
POSITION_AFTER_AN = "after_an"          # ST 3：作者注（AN）下
POSITION_AT_DEPTH = "at_depth"          # ST 4：按 depth 插入对话历史
POSITION_EM_TOP = "em_top"              # ST 5：示例消息上
POSITION_EM_BOTTOM = "em_bottom"        # ST 6：示例消息下
POSITION_OUTLET = "outlet"              # ST 7：出口（不进 prompt）

#: 全部合法档位
POSITIONS = frozenset(
    {
        POSITION_BEFORE_CHAR,
        POSITION_AFTER_CHAR,
        POSITION_BEFORE_AN,
        POSITION_AFTER_AN,
        POSITION_AT_DEPTH,
        POSITION_EM_TOP,
        POSITION_EM_BOTTOM,
        POSITION_OUTLET,
    }
)

#: 可注入的消息角色（与 OpenAI messages 的 role 对齐）
ROLES = frozenset({"system", "user", "assistant"})


class WorldBookEntry(BaseModel):
    """世界书条目。

    keys      关键词列表：对话文本中出现任一关键词（默认不区分大小写）即触发；
    regex     可选正则模式列表：任一模式 search 命中即触发（与关键词互为补充）；
    vector_text     语义触发文本：非空即启用向量通道。它与用户输入分别编码后算
                    余弦相似度，≥ vector_threshold 即触发——用于捕捉关键词
                    覆盖不到的同义改写（如「心里空落落的」之于「孤独」）；
    content   触发后注入 prompt 的正文；
    scope     归属：`*` = 对所有陪伴对象生效；填角色 id = 只对该角色生效
              （用户给某个自建角色写的专属设定用这个，避免串味到别的角色）；
    enabled   是否参与触发匹配（可整体停用某条目）；
    case_sensitive  关键词是否区分大小写（默认 False = 不区分）；
    priority  注入排序权重（越大越靠前）。

    三通道是「或」的关系：任一命中即算条目命中。只用关键词的条目不受影响。

    vector_threshold 必须按**当前 embedding 模型**校准，不能套用别家的值。
    实测 Qwen3-Embedding-8B：语义命中落在 0.74–0.90，而无关文本也高至 0.65
    （该模型相似度基线普遍偏高），故默认 0.7 才分得开；
    本地 deterministic 实现相似度仅 0.0–0.2，完全不适用该阈值
    （两种 provider 的分布不同，详见 embedding.py）。
    三通道是「或」的关系，任一命中即算条目命中；`constant` 条目无需命中，恒注入。

    priority 与 order 的区别：priority 是**本项目**的「这条设定比那条重要」，
    由用户与内置内容决定；order 是**来源格式**自带的次序（酒馆世界书的 order），
    宿主只如实保留、不改写。同 priority 时按 order 升序，再按加载顺序稳定。
    """

    id: str = Field(description="条目唯一标识（英文小写连字符）")
    title: str = Field(description="条目标题")
    scope: str = Field(
        default=SCOPE_ALL,
        description=(
            "归属：`*` = 对所有陪伴对象生效；填角色 id = 只对该角色生效。"
            "内置条目按**内容归属**：写某个内置角色生活/设定的条目填该角色 id，"
            "与角色无关的通用条目才留 `*`；用户自建条目的「归属角色是否存在」由 StudioStore 校验"
        ),
    )
    content: str = Field(description="触发后注入的正文")
    keys: list[str] = Field(default_factory=list, description="关键词列表")
    regex: list[str] = Field(default_factory=list, description="正则触发模式列表")
    vector_text: str = Field(
        default="", description="语义触发文本（非空即启用向量通道）"
    )
    vector_threshold: float = Field(
        default=0.7,
        ge=-1.0,
        le=1.0,
        description="向量通道命中阈值（余弦相似度，越接近 1 越严格）",
    )
    enabled: bool = Field(default=True, description="是否参与触发")
    case_sensitive: bool = Field(default=False, description="关键词匹配是否区分大小写")
    priority: int = Field(default=0, description="注入优先级（越大越靠前）")
    constant: bool = Field(
        default=False,
        description=(
            "常驻：不需要任何触发条件，每轮都注入（ST 世界书的 constant）。"
            "真实酒馆世界里近一半条目是常驻型，不认这个字段就会直接读不到"
        ),
    )
    position: str = Field(
        default=POSITION_AFTER_CHAR,
        description=(
            "注入档位（POSITION_* 之一，对齐 ST 的 world_info_position）；"
            "来源格式自带的「插在哪里」，宿主如实保留"
        ),
    )
    depth: int = Field(
        default=4,
        ge=0,
        le=10_000,
        description="position == at_depth 时，插入到对话历史倒数第几条之前（ST 的 depth）",
    )
    order: int = Field(
        default=100,
        description=(
            "同档位内的次序（小者先）；来源格式自带的排序键（ST 的 order）。"
            "与 priority 的区别见类文档"
        ),
    )
    role: str = Field(
        default="system",
        description=(
            "`position == at_depth` 时注入用的消息角色：system / user / assistant。"
            "其余档位忽略（ST 世界书的 role 也只对按深度插入生效）"
        ),
    )

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        """归属取值校验：`*` 或一个格式合法的角色 id。

        只校验**格式**：角色是否存在属于本地数据的一致性检查，
        由 StudioStore 在写入时负责（模型层不依赖存储）。
        """
        text = (value or "").strip()
        if text == SCOPE_ALL:
            return text
        if not _SCOPE_PATTERN.match(text):
            raise ValueError(
                f"非法的归属取值：{value!r}"
                "（只允许 `*`，或由小写字母/数字/-/_ 组成、以字母或数字开头的角色 id）"
            )
        return text

    @field_validator("position")
    @classmethod
    def _validate_position(cls, value: str) -> str:
        """档位取值校验：必须是 POSITIONS 之一（大小写宽容，统一存小写）。"""
        text = (value or "").strip().lower()
        if text not in POSITIONS:
            raise ValueError(
                f"非法的注入档位：{value!r}（可选：{', '.join(sorted(POSITIONS))}）"
            )
        return text

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        """消息角色校验：只接受 OpenAI messages 的三种角色（大小写宽容）。"""
        text = (value or "").strip().lower()
        if text not in ROLES:
            raise ValueError(f"非法的注入角色：{value!r}（可选：{', '.join(sorted(ROLES))}）")
        return text

    def applies_to(self, companion_id: str) -> bool:
        """该条目是否对指定陪伴对象生效（`*` 归属对所有人都生效）。"""
        return self.scope == SCOPE_ALL or self.scope == companion_id

    @property
    def vector_enabled(self) -> bool:
        """是否启用语义向量触发通道（声明了非空 vector_text）。"""
        return bool(self.vector_text.strip())

    @property
    def has_trigger(self) -> bool:
        """是否具备触发条件（关键词 / 正则 / 语义向量任一，或常驻）。

        `constant` 条目**无需**触发词：它在酒馆世界里就是每轮注入的设定，
        要求它必须有 keys 会把真实世界书里一半以上的条目判为非法。
        """
        return self.constant or bool(self.keys or self.regex or self.vector_enabled)
