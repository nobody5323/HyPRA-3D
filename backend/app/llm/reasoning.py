"""推理模型的识别与长度下限。

背景（实测）：推理模型会先输出**思考内容**（`reasoning_content`），思考长度常达
1500+ token。若 `max_tokens` 偏小，额度会被思考全部吃光，正文为空
（`finish_reason=length`）——表现为「模型不回复」，而且因为 HTTP 是 200、
异常也没有，静默得让人查不出原因。

本模块只做两件事：

1. `is_reasoning_model()`：按**模型名**保守匹配。宁缺毋滥 —— 猜错只是给多了额度，
   漏判则由 provider 的运行时观测（`saw_reasoning`）补上；
2. `ensure_reasoning_headroom()`：给推理模型一个 `max_tokens` 下限。

为什么需要下限：`max_tokens` 在这里同时约束「思考 + 正文」。项目内置档把它压到
400 是「防长篇大论」的合理取舍，但对推理模型会直接压成空回复，因此必须放宽。
"""

from __future__ import annotations

#: 视为推理模型的模型名片段（小写匹配）
REASONING_MARKERS = (
    "reasoner",
    "reasoning",
    "thinking",
    "-r1",
    "r1-",
    "qwq",
    "-o1",
    "o1-",
    "-o3",
    "o3-",
)

#: 推理模型的 max_tokens 下限。
#: 实测思考约 1458 token，留出正文空间后取 3000（仍低于常见 8k 上限）。
REASONING_MIN_TOKENS = 3000


def is_reasoning_model(model: str) -> bool:
    """模型名是否像推理模型（保守匹配，不联网探测）。"""
    name = (model or "").lower()
    return any(marker in name for marker in REASONING_MARKERS)


def ensure_reasoning_headroom(
    max_tokens: int | None, model: str, *, assume_reasoning: bool = False
) -> int | None:
    """给推理模型抬高 `max_tokens` 下限；其它模型原样返回。

    `assume_reasoning=True` 供调用方已从**运行期证据**（响应里出现过
    `reasoning_content`）确认了推理模型、但模型名里没有标记的情形
    （实测 `deepseek-flash` 就是这种）。

    `max_tokens=None`（未设置上限）时也给出下限：不设上限的推理模型更容易
    长篇思考，显式兜一个值便于预算控制。
    """
    if not (assume_reasoning or is_reasoning_model(model)):
        return max_tokens
    if max_tokens is None:
        return REASONING_MIN_TOKENS
    return max(max_tokens, REASONING_MIN_TOKENS)
