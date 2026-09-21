"""去重（MD5 + MinHash）测试。"""

import pytest

from app.memory.knowledge.dedup import (
    content_digest,
    file_digest,
    find_similar,
    jaccard_estimate,
    minhash_signature,
)

LONG_TEXT = (
    "苏澄的咨询室在老城区一栋安静的二层小楼里，浅米色墙面，一张墨绿沙发和"
    "两把布艺椅围成不压迫的夹角，茶几上常年放着一壶温热的茉莉花茶与一盒纸巾。"
    "窗外是梧桐树，午后阳光透过百叶窗落下条纹光影。来访的人常说，走进这间"
    "屋子，语速会不自觉地慢下来。"
)
SLIGHTLY_EDITED = LONG_TEXT.replace("茉莉花茶", "菊花茶")
UNRELATED = (
    "量子纠缠描述了粒子之间非局域的关联，与经典物理的直觉相冲突，"
    "近年的实验已经验证了贝尔不等式的违反。"
)


# ---------- 一级 / 二级：精确去重 ----------


def test_file_digest_is_deterministic() -> None:
    assert file_digest(b"hello") == file_digest(b"hello")
    assert file_digest(b"hello") != file_digest(b"hello!")


def test_content_digest_ignores_whitespace_differences() -> None:
    """换行风格与多余空白不该影响「同内容」判定（同一文档的不同导出格式）。"""
    assert content_digest("小林\n喜欢下雨天") == content_digest("小林   喜欢下雨天")
    assert content_digest("小林喜欢") != content_digest("小林偏爱")


# ---------- 三级：MinHash 近似去重 ----------


def test_signature_is_deterministic() -> None:
    """签名必须可复现——否则去重结果会随进程/环境漂移。"""
    assert minhash_signature(LONG_TEXT) == minhash_signature(LONG_TEXT)


def test_identical_text_scores_one() -> None:
    assert jaccard_estimate(
        minhash_signature(LONG_TEXT), minhash_signature(LONG_TEXT)
    ) == 1.0


def test_slight_edit_keeps_high_similarity() -> None:
    """只改几个字仍应高度相似——精确哈希在这种场景下完全失效。"""
    score = jaccard_estimate(
        minhash_signature(LONG_TEXT), minhash_signature(SLIGHTLY_EDITED)
    )
    assert score > 0.7


def test_unrelated_text_scores_low() -> None:
    score = jaccard_estimate(
        minhash_signature(LONG_TEXT), minhash_signature(UNRELATED)
    )
    assert score < 0.3


def test_whitespace_and_layout_do_not_affect_signature() -> None:
    """排版差异（换行、缩进）不该影响判重：同一文档不同排版应视为相同。"""
    reflowed = LONG_TEXT.replace("，", "，\n")
    assert jaccard_estimate(
        minhash_signature(LONG_TEXT), minhash_signature(reflowed)
    ) == 1.0


def test_find_similar_filters_by_threshold() -> None:
    base = minhash_signature(LONG_TEXT)
    candidates = [
        ("same", minhash_signature(LONG_TEXT)),
        ("edited", minhash_signature(SLIGHTLY_EDITED)),
        ("other", minhash_signature(UNRELATED)),
    ]

    hits = find_similar(base, candidates, threshold=0.95)
    assert [doc_id for doc_id, _ in hits] == ["same"]


def test_find_similar_orders_by_score() -> None:
    base = minhash_signature(LONG_TEXT)
    candidates = [
        ("other", minhash_signature(UNRELATED)),
        ("same", minhash_signature(LONG_TEXT)),
    ]

    hits = find_similar(base, candidates, threshold=0.0)
    assert [doc_id for doc_id, _ in hits] == ["same", "other"]


def test_signature_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        jaccard_estimate(
            minhash_signature("a" * 50), minhash_signature("b" * 50, count=64)
        )


def test_empty_text_signature() -> None:
    """空文本给出定长零签名；两个空文本相似度为 1（上层应在此之前拒绝空文档）。"""
    signature = minhash_signature("")

    assert len(signature) == 128
    assert jaccard_estimate(signature, minhash_signature("   ")) == 1.0
