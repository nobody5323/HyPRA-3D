"""RRF 融合测试。"""

import pytest

from app.rag.retrieval.hybrid import DEFAULT_RRF_K, reciprocal_rank_fusion


def test_default_k_is_60() -> None:
    """RRF 原论文推荐值。"""
    assert DEFAULT_RRF_K == 60


def test_single_ranking_keeps_order() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    assert [doc_id for doc_id, _ in fused] == ["a", "b", "c"]


def test_scores_follow_rrf_formula() -> None:
    k = 60
    fused = dict(reciprocal_rank_fusion([["a", "b"]], k=k))
    assert fused["a"] == pytest.approx(1 / (k + 1))
    assert fused["b"] == pytest.approx(1 / (k + 2))


def test_agreement_across_channels_wins() -> None:
    """同时被两个通道召回的文档排名最高（RRF 的核心价值）。"""
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert fused[0][0] == "b"


def test_dedupes_within_a_channel() -> None:
    """同一通道内重复的 doc_id 只计一次（防御上游重复）。"""
    fused = reciprocal_rank_fusion([["a", "a", "b"]])
    assert dict(fused)["a"] == pytest.approx(1 / 61)
    assert [doc_id for doc_id, _ in fused] == ["a", "b"]


def test_tie_breaks_by_first_seen_channel() -> None:
    """同分时前一通道优先（稳定排序，结果可复现）。"""
    fused = reciprocal_rank_fusion([["a"], ["b"]])
    assert [doc_id for doc_id, _ in fused] == ["a", "b"]


def test_empty_inputs() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_negative_k_rejected() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], k=-1)


def test_zero_k_allowed() -> None:
    """k=0 在数学上合法（退化为 1/rank）。"""
    fused = dict(reciprocal_rank_fusion([["a"]], k=0))
    assert fused["a"] == pytest.approx(1.0)
