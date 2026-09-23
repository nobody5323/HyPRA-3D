"""情绪识别工具：function calling 结构化输出。

统一情绪标签：识别「用户此刻的情绪」，一处产出、三处消费——
    ① 状态变量 {{current_mood}}（人设动态注入，下一轮生效）
    ② 记忆加权（冷层事实 emotion_tag + 温层召回加权）
    ③ 3D 数字人表情联动（做共情式回应表情）

主通道：LLM function calling（`llm_channel.py` 提供工具 schema 与结果解析）；
兜底通道：可替换策略（`fallback_regex.py` 关键词规则 / `fallback_neutral.py` 恒中性）。

拆分自原 `emotion.py`（`AGENTS.md §9.11` P2）。**本模块是兼容层**：
既有调用方（`graph/nodes.py`、`llm/mock.py`、`digital_human/*`、`tools/builtin_tools.py`）
与测试都按旧路径 `from app.tools.emotion import ...` import，重导出让拆分对它们完全透明。
"""

from app.tools.emotion.base import (
    EMOTION_FACIAL_EXPRESSIONS,
    EMOTION_LABELS_ZH,
    FALLBACK_REPLY,
    EmotionFallback,
    EmotionLabel,
    EmotionResult,
)
from app.tools.emotion.fallback_neutral import NeutralFallback
from app.tools.emotion.fallback_regex import RegexFallback, has_emotion_keyword
from app.tools.emotion.factory import (
    FALLBACKS,
    create_fallback,
    default_fallback,
    extract_emotion_fallback,
)
from app.tools.emotion.llm_channel import (
    EMOTION_TOOL_NAME,
    build_emotion_tool,
    parse_emotion_result,
)

__all__ = [
    "EMOTION_FACIAL_EXPRESSIONS",
    "EMOTION_LABELS_ZH",
    "EMOTION_TOOL_NAME",
    "FALLBACK_REPLY",
    "FALLBACKS",
    "EmotionFallback",
    "EmotionLabel",
    "EmotionResult",
    "NeutralFallback",
    "RegexFallback",
    "build_emotion_tool",
    "create_fallback",
    "default_fallback",
    "extract_emotion_fallback",
    "has_emotion_keyword",
    "parse_emotion_result",
]
