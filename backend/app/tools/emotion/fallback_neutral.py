"""中性兜底：不做任何判断，直接返回 NEUTRAL。

**什么时候用它**：宁可中性，也不要误判。

正则规则只看字面，读不懂否定——「我不开心」会命中 HAPPY 的「开心」。而情绪标签会同时
驱动 3D 表情与记忆加权（写进冷层事实的 emotion_tag），判反比不判的代价大。
把 `emotion_fallback` 配成 `neutral` 即可关闭关键词判断，链路仍然不空转
（仍返回通用承接回复，只是情绪恒为中性、置信度更低）。
"""

from __future__ import annotations

from app.tools.emotion.base import (
    FALLBACK_REPLY,
    EmotionFallback,
    EmotionLabel,
    EmotionResult,
)


class NeutralFallback(EmotionFallback):
    """不做判断，直接中性。"""

    name = "neutral"

    def detect(self, text: str) -> EmotionResult:  # noqa: ARG002 - 接口约定需要该参数
        return EmotionResult(
            reply=FALLBACK_REPLY,
            emotion=EmotionLabel.NEUTRAL,
            intensity=0.5,
            confidence=0.3,
            evidence="未启用关键词兜底",
            source="fallback",
        )
