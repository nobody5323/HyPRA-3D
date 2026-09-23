"""AI 预设适配的数据模型（规则层产出 → 规划层消费）。

三个概念要分清：

- `Finding`：一条「体检发现」——某个位置命中了某条规则，带证据与处置动作；
- `RewriteTask`：需要模型语义改写的条目（攒成一批，一次调用集中处理）；
- `DetectionResult`：把发现、确定性补丁、改写任务打包，是规则层与规划层之间的唯一契约。

为什么把「确定性修复」与「模型改写」分开（见 detector.py 的模块说明）：
一份预设动辄上百条、正文上万字，全部交模型既贵又容易漏；而阈值超标、
宏里写了标签这类是确定性判断，不该占用模型调用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

#: 处置动作（与 rules.yaml 的 action 一一对应）
#: - auto_patch   规则层直接生成覆盖层补丁
#: - llm_rewrite  需要模型改写条目正文
#: - preserve     内容与开关都不动，只建议调整注入位置
#: - review       只提示，交用户决定
Action = Literal["auto_patch", "llm_rewrite", "preserve", "review"]

#: 严重度（界面排序与配色用）
Severity = Literal["high", "medium", "low"]

#: auto_patch 的修法标识（detector 按此分发）
FixKind = Literal[
    "disable_thinking",
    "clamp_int",
    "disable_entry",
    "clear_thinking_macro",
    "clamp_reply_length",
    "flip_reply_length_bound",
]


# --------------------------------------------------------------------------
# 规则配置（从 rules.yaml 加载）
# --------------------------------------------------------------------------


class TargetProfile(BaseModel):
    """适配目标：HyPRA 的陪伴对话形态。

    这些值是**建议值**，规则层的确定性修复会以它们为准（如把「不少于 2500 字」
    钳到 `reply_length`）。用户可在 rules.yaml 里按需调整。
    """

    person: str = Field(default="second_person", description="人称口径")
    #: 只输出说的话（不写动作/神情/环境/心理描写）。小说预设默认要写这些，
    #: 所以它同时影响 meta-prompt 的措辞与「以行动结尾」这类条目的改写方向。
    dialogue_only: bool = Field(default=True, description="只输出对话，不写描写")
    reply_length: int = Field(default=100, gt=0, description="单次回复字数上限")
    thinking: bool = Field(default=False, description="是否保留推理模式")
    max_tokens: int = Field(default=1024, gt=0, description="回复长度上限")
    max_context: int = Field(default=32768, gt=0, description="提示词总量预算")


class PresetRule(BaseModel):
    """预设级规则：判定预设顶层字段。"""

    id: str
    field: str = Field(description="被判定的预设字段名（如 show_thoughts）")
    equals: Any = Field(default=None, description="等于该值时命中")
    greater_than: float | None = Field(default=None, description="大于该值时命中")
    severity: Severity = "medium"
    action: Action
    message: str
    fix: str = Field(default="", description="auto_patch 时的修法标识")

    @field_validator("equals", "greater_than")
    @classmethod
    def _require_condition(cls, value: Any, info) -> Any:
        return value

    def matches(self, value: Any) -> bool:
        """该字段取值是否触发本条规则。"""
        if self.greater_than is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            return float(value) > self.greater_than
        if self.equals is not None:
            return value == self.equals
        return False


class ContentRule(BaseModel):
    """条目级规则：判定条目正文（正则命中即触发）。"""

    id: str
    regex: str
    severity: Severity = "medium"
    action: Action
    message: str
    anchor: str = Field(default="", description="preserve 类：建议归拢到的槽位 identifier")
    fix: str = Field(default="", description="auto_patch 时的修法标识")

    @field_validator("regex")
    @classmethod
    def _must_compile(cls, value: str) -> str:
        """规则写错正则该早失败，而不是等到扫描预设时才炸。"""
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"正则非法：{value!r}（{exc}）") from exc
        return value


class AdaptationRules(BaseModel):
    """一份适配规则集（对应 rules.yaml）。"""

    version: int = 1
    target: TargetProfile = Field(default_factory=TargetProfile)
    preset_rules: list[PresetRule] = Field(default_factory=list)
    content_rules: list[ContentRule] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 检测产出
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """一条体检发现。"""

    rule_id: str
    severity: Severity
    action: Action
    scope: str          # preset（预设级） | content（条目级）
    identifier: str     # 命中条目的 identifier；预设级为空串
    name: str           # 条目展示名（界面阅读用）
    evidence: str       # 命中的原文片段（已截断）
    message: str        # 人类可读说明
    anchor: str = ""    # preserve 类：建议归拢到的槽位


@dataclass
class RewriteTask:
    """需要模型改写的条目（一批一次调用）。"""

    identifier: str
    name: str
    content: str
    issues: list[str] = field(default_factory=list)     # 命中的问题说明（去重后）
    #: 命中的规则 id：改写后复查用（确认问题真的消失了，而不是模型敷衍了事）
    rule_ids: list[str] = field(default_factory=list)

    def add_issue(self, rule_id: str, message: str) -> None:
        """登记一条问题（同一规则重复命中只记一次）。"""
        if message not in self.issues:
            self.issues.append(message)
        if rule_id not in self.rule_ids:
            self.rule_ids.append(rule_id)


@dataclass
class DetectionResult:
    """规则层对一份预设的完整体检结果。"""

    findings: list[Finding] = field(default_factory=list)
    #: 规则层能确定性生成的覆盖层补丁（可直接交给 store.patch_override）
    auto_patch: dict[str, Any] = field(default_factory=dict)
    #: 需要模型改写的条目
    rewrite_tasks: list[RewriteTask] = field(default_factory=list)
    #: 内容保留、只建议调位置的条目（NSFW / 破限类）
    preserved: list[Finding] = field(default_factory=list)
    #: 规则层的提示（如「因保护规则而未关闭某条目」）
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        """按严重度统计命中数（界面上的「发现 N 个问题」）。"""
        counts = {"high": 0, "medium": 0, "low": 0}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        return counts

    @property
    def has_auto_patch(self) -> bool:
        """规则层是否产出了可直接应用的补丁。"""
        return bool(self.auto_patch)


@dataclass
class RewriteOutcome:
    """单条改写的处置结果（供界面逐条展示「已改 / 已拒 / 缺失」）。"""

    identifier: str
    name: str
    status: str          # applied | rejected | missing | skipped
    reason: str = ""
    new_content: str = ""


@dataclass
class PlanResult:
    """规划层的最终产出：一份可直接应用的覆盖层补丁 + 逐条处置记录。

    `patch` 已把规则层的确定性修复与模型的条目改写合并完毕，可直接交给
    `StPresetStore.patch_override()`；`rewrites` 用于向用户解释「模型动了什么」。
    """

    patch: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    preserved: list[Finding] = field(default_factory=list)
    rewrites: list[RewriteOutcome] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    model_used: bool = False

    @property
    def applied_count(self) -> int:
        """成功改写的条目数。"""
        return sum(1 for item in self.rewrites if item.status == "applied")
