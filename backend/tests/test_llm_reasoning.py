"""推理模型识别与长度下限的测试。

背景：推理模型会先用掉大量 token 思考，`max_tokens` 偏小会让正文为空。
这里只测「识别 + 抬高下限」这两个纯函数（provider 侧的运行期观测见
tests/test_llm_providers.py）。
"""

import pytest

from app.llm.reasoning import (
    REASONING_MIN_TOKENS,
    ensure_reasoning_headroom,
    is_reasoning_model,
)


@pytest.mark.parametrize(
    "model",
    [
        "deepseek-reasoner",
        "deepseek-r1",
        "qwq-32b",
        "qwen3-8b-thinking",
        "o1-mini",
    ],
)
def test_known_reasoning_models_detected(model: str) -> None:
    """常见的推理模型名应被识别。"""
    assert is_reasoning_model(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "qwen2.5-7b-instruct",
        "deepseek-chat",
        "gpt-4o-mini",
        "",
    ],
)
def test_ordinary_models_not_flagged(model: str) -> None:
    """普通对话模型不得被误判（否则会凭空放宽长度上限）。"""
    assert is_reasoning_model(model) is False


def test_headroom_raises_small_limit() -> None:
    """小额度必须被抬到下限。"""
    assert ensure_reasoning_headroom(350, "deepseek-reasoner") == REASONING_MIN_TOKENS


def test_headroom_keeps_larger_limit() -> None:
    """已经够大时不动它（只兜下限，不压低用户的选择）。"""
    assert ensure_reasoning_headroom(8000, "deepseek-reasoner") == 8000


def test_headroom_fills_missing_limit() -> None:
    """未设置上限时也给出下限，便于预算控制。"""
    assert ensure_reasoning_headroom(None, "deepseek-reasoner") == REASONING_MIN_TOKENS


def test_headroom_ignores_ordinary_model() -> None:
    """普通模型原样返回（含 None）。"""
    assert ensure_reasoning_headroom(350, "qwen2.5-7b-instruct") == 350
    assert ensure_reasoning_headroom(None, "qwen2.5-7b-instruct") is None


def test_headroom_applies_when_reasoning_observed_at_runtime() -> None:
    """模型名不带标记、但调用方已从响应里观测到思考 → 同样抬高下限。

    回归用例：`ensure_reasoning_headroom` 内部再判一次模型名，会把
    「名字里没有标记的推理模型」（实测 deepseek-flash）短路掉。
    """
    assert (
        ensure_reasoning_headroom(350, "deepseek-flash", assume_reasoning=True)
        == REASONING_MIN_TOKENS
    )
