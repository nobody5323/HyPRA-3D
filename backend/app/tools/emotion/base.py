"""情绪标签、结构化结果与兜底策略接口。

拆分自原 `emotion.py`（`AGENTS.md §9.11` P2）。统一情绪标签「一处产出、三处消费」：

    ① 状态变量 {{current_mood}}（人设动态注入，下一轮生效）
    ② 记忆加权（冷层事实 emotion_tag + 温层召回加权）
    ③ 3D 数字人表情联动（做共情式回应表情）

**这里只把接口开在兜底通道上**，理由：产出情绪有两条路——LLM function calling（主）
与规则/正则（兜底）。主通道不是「策略」而是产品前提，关掉它情绪链路就不成立了
（属 §9.2 的 core，不可禁用）；真正需要可替换的是「模型没给结构化输出时怎么办」，
所以策略落在 `EmotionFallback` 上。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field


class EmotionLabel(str, Enum):
    """情绪标签（8 类，覆盖情感陪伴高频场景）。"""

    HAPPY = "happy"
    CALM = "calm"
    SAD = "sad"
    ANXIOUS = "anxious"
    TIRED = "tired"
    ANGRY = "angry"
    SURPRISED = "surprised"
    NEUTRAL = "neutral"


# 英文标签 → 中文（供 {{current_mood}} 状态变量与前端展示）
EMOTION_LABELS_ZH: dict[str, str] = {
    EmotionLabel.HAPPY.value: "开心",
    EmotionLabel.CALM.value: "平静",
    EmotionLabel.SAD.value: "难过",
    EmotionLabel.ANXIOUS.value: "焦虑",
    EmotionLabel.TIRED.value: "疲惫",
    EmotionLabel.ANGRY.value: "生气",
    EmotionLabel.SURPRISED.value: "惊讶",
    EmotionLabel.NEUTRAL.value: "平静",
}

# 情绪 → 3D 表情键（数字人联动使用；本模块仅产出数据）
EMOTION_FACIAL_EXPRESSIONS: dict[str, str] = {
    EmotionLabel.HAPPY.value: "smile",
    EmotionLabel.CALM.value: "gentle",
    EmotionLabel.SAD.value: "downcast",
    EmotionLabel.ANXIOUS.value: "frowning_worry",
    EmotionLabel.TIRED.value: "droopy_eyes",
    EmotionLabel.ANGRY.value: "frown",
    EmotionLabel.SURPRISED.value: "wide_eyes",
    EmotionLabel.NEUTRAL.value: "default",
}

#: 兜底时用于生成的最小回复（模型不可用时保证不空转）
FALLBACK_REPLY = "我在这儿，听着呢。你愿意多说一点吗？"


class EmotionResult(BaseModel):
    """一次结构化输出：助手回复 + 用户情绪判定。"""

    reply: str = Field(description="给用户的回复正文")
    emotion: EmotionLabel = Field(default=EmotionLabel.NEUTRAL, description="用户此刻情绪")
    intensity: float = Field(default=0.5, ge=0.0, le=1.0, description="情绪强度 0-1")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="判定置信度 0-1")
    evidence: str = Field(default="", description="判定依据（简短，便于调试与展示）")
    source: str = Field(default="llm", description="来源：llm（结构化输出）| fallback（兜底）")

    @property
    def label_zh(self) -> str:
        """中文标签（写入 current_mood 状态变量用）。"""
        return EMOTION_LABELS_ZH.get(self.emotion.value, "平静")

    @property
    def facial_expression(self) -> str:
        """3D 表情键（数字人联动消费）。"""
        return EMOTION_FACIAL_EXPRESSIONS.get(self.emotion.value, "default")


class EmotionFallback(ABC):
    """主通道失效时的兜底策略（一种取舍一个实现）。"""

    #: 实现名（与配置值一致）
    name: str = ""

    @abstractmethod
    def detect(self, text: str) -> EmotionResult:
        """给出情绪判定。

        **永不失败**：无法判定时返回 NEUTRAL，而不是抛错或返回 None——
        调用方正处在「模型没给结构化输出」的路径上，需要一个总能落地的结果。
        """
