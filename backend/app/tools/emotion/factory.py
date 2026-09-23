"""兜底策略工厂：按配置选择实现。

`emotion_fallback`（见 `app/config.py`）：

- `regex`（默认）—— 关键词/正则判定，能捕捉明显情绪，但可能在否定句上判反；
- `neutral`      —— 不判定，恒为中性；情绪只由主通道（LLM）决定。
"""

from __future__ import annotations

from app.tools.emotion.base import EmotionFallback, EmotionResult
from app.tools.emotion.fallback_neutral import NeutralFallback
from app.tools.emotion.fallback_regex import RegexFallback

REGEX = "regex"
NEUTRAL = "neutral"

#: 可选值（供报错信息与文档引用）
FALLBACKS: tuple[str, ...] = (REGEX, NEUTRAL)


def create_fallback(backend: str = REGEX) -> EmotionFallback:
    """按名字创建兜底策略。

    抛出:
        ValueError: 策略名未知。
    """
    name = (backend or REGEX).strip().lower()
    if name in {"", REGEX}:
        return RegexFallback()
    if name == NEUTRAL:
        return NeutralFallback()
    raise ValueError(f"未知兜底策略：{backend!r}（可选 {' | '.join(FALLBACKS)}）")


def default_fallback() -> EmotionFallback:
    """按当前配置取兜底策略（读 `settings.emotion_fallback`）。"""
    from app.config import get_settings

    return create_fallback(get_settings().emotion_fallback)


def extract_emotion_fallback(text: str) -> EmotionResult:
    """关键词/正则兜底提取（既有调用方入口，签名与语义不变）。

    永不失败：无法判定时返回 NEUTRAL 情绪 + 通用承接回复。
    """
    return default_fallback().detect(text)
