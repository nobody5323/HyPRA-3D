"""主通道：LLM function calling 的工具定义与结果解析。

职责只有两件事——**给模型一个填情绪的工具**，以及**把模型填的结果校验成对象**。
它不看文本猜情绪：理解由模型完成，本模块负责让输出可靠可解析。
"""

from __future__ import annotations

import json

from app.tools.emotion.base import EmotionLabel, EmotionResult

#: function calling 工具名
EMOTION_TOOL_NAME = "report_reply_and_emotion"


def build_emotion_tool() -> dict:
    """OpenAI function calling 工具定义（tools 参数格式）。"""
    return {
        "type": "function",
        "function": {
            "name": EMOTION_TOOL_NAME,
            "description": (
                "在回复用户的同时，判定用户此刻的情绪。"
                "必须先给出回复，再根据用户最新发言判断情绪类别与强度。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reply": {
                        "type": "string",
                        "description": "给用户的回复正文（保持角色人设与语气）",
                    },
                    "emotion": {
                        "type": "string",
                        "enum": [label.value for label in EmotionLabel],
                        "description": "用户此刻的情绪标签",
                    },
                    "intensity": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "情绪强度（0 轻微，1 强烈）",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "情绪判定置信度",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "判定依据（引用用户原话中的关键词，简短）",
                    },
                },
                "required": ["reply", "emotion"],
            },
        },
    }


def parse_emotion_result(raw_arguments: str) -> EmotionResult | None:
    """解析工具调用的 arguments（JSON 字符串）→ EmotionResult。

    解析失败（非法 JSON / 缺字段 / 取值越界）返回 None，由调用方降级到兜底通道。
    """
    try:
        payload = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not payload.get("reply"):
        return None
    try:
        result = EmotionResult.model_validate({**payload, "source": "llm"})
    except Exception:
        return None
    return result
