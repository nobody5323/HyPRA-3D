"""兜底通道：关键词 / 正则规则。

代价要说清楚：规则只看**字面**，读不懂否定——「我不开心」会命中 HAPPY 的「开心」，
把情绪判反。如果业务上更怕判错（情绪会驱动表情与记忆加权），
把 `emotion_fallback` 切成 `neutral` 即可（见 `fallback_neutral.py`）。
"""

from __future__ import annotations

import re

from app.tools.emotion.base import (
    FALLBACK_REPLY,
    EmotionFallback,
    EmotionLabel,
    EmotionResult,
)

# (情绪标签, 正则模式元组, 基础强度) —— 按顺序匹配，首个命中即返回
# 用正则而非子串：支持程度副词（「压力好大」「太累了」）与常见变体。
_FALLBACK_RULES: list[tuple[EmotionLabel, tuple[str, ...], float]] = [
    (
        EmotionLabel.ANXIOUS,
        (
            r"焦虑", r"紧张", r"担心", r"害怕", r"不安", r"心慌", r"慌",
            r"压力(?:好|很|特别|太|挺)?大", r"失眠", r"睡不着", r"睡不着", r"睡不好",
        ),
        0.7,
    ),
    (
        EmotionLabel.SAD,
        (r"难过", r"伤心", r"想哭", r"哭了?", r"委屈", r"失恋", r"失落", r"沮丧", r"低落"),
        0.7,
    ),
    (
        EmotionLabel.TIRED,
        (r"累", r"疲惫", r"好困", r"撑不住", r"没力气", r"精疲力尽", r"熬夜", r"没睡"),
        0.6,
    ),
    (
        EmotionLabel.ANGRY,
        (r"生气", r"愤怒", r"气死", r"火大", r"讨厌", r"烦死", r"烦躁"),
        0.7,
    ),
    (
        EmotionLabel.HAPPY,
        (r"开心", r"高兴", r"太好了", r"哈哈", r"兴奋", r"喜欢", r"谢谢", r"顺利"),
        0.6,
    ),
    (
        EmotionLabel.SURPRISED,
        (r"没想到", r"居然", r"竟然", r"惊讶", r"天啊"),
        0.6,
    ),
    (
        EmotionLabel.CALM,
        (r"平静", r"还好", r"挺好的", r"放松", r"踏实"),
        0.5,
    ),
]


class RegexFallback(EmotionFallback):
    """关键词 / 正则兜底（默认策略）。"""

    name = "regex"

    def detect(self, text: str) -> EmotionResult:
        for label, patterns, intensity in _FALLBACK_RULES:
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    return EmotionResult(
                        reply=FALLBACK_REPLY,
                        emotion=label,
                        intensity=intensity,
                        confidence=0.4,  # 兜底通道置信度低，便于后续被 LLM 结果覆盖
                        evidence=f"关键词命中：{match.group(0)}",
                        source="fallback",
                    )
        return EmotionResult(
            reply=FALLBACK_REPLY,
            emotion=EmotionLabel.NEUTRAL,
            intensity=0.5,
            confidence=0.3,
            evidence="未命中情绪关键词",
            source="fallback",
        )


def has_emotion_keyword(text: str) -> bool:
    """文本是否含任一情绪关键词（供调试/测试用）。"""
    return any(
        re.search(pattern, text)
        for _, patterns, _ in _FALLBACK_RULES
        for pattern in patterns
    )
