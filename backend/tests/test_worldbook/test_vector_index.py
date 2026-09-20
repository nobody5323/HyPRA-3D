"""世界书语义向量索引测试（假 provider，无网络）。"""

import math

from app.memory.warm.embedding import EmbeddingProvider
from app.worldbook.models import WorldBookEntry
from app.worldbook.vector_index import WorldBookVectorIndex


def _normalize(vector: list[float]) -> list[float]:
    """测试用 L2 归一化。

    cosine_similarity 是点积实现，约定 provider 输出已归一化，
    因此假 provider 也必须遵守该契约（否则相似度会被算成内积）。
    """
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0.0:
        return vector
    return [value / length for value in vector]


class _FakeProvider(EmbeddingProvider):
    """按字典返回预设向量的假 provider；未登记的文本返回零向量。

    fail_on 中的文本会抛异常，用于验证编码失败时的降级行为。
    """

    def __init__(
        self,
        vectors: dict[str, list[float]],
        *,
        dim: int = 3,
        fail_on: set[str] | None = None,
    ) -> None:
        self._vectors = vectors
        self._dim = dim
        self._fail_on = fail_on or set()

    @property
    def dimension(self) -> int:
        return self._dim

    def _lookup(self, text: str) -> list[float]:
        if text in self._fail_on:
            raise RuntimeError("模拟编码失败")
        return _normalize(self._vectors.get(text, [0.0] * self._dim))

    def embed(self, text: str) -> list[float]:
        return self._lookup(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._lookup(text) for text in texts]


def _entry(**overrides) -> WorldBookEntry:
    base = {
        "id": "demo",
        "title": "演示条目",
        "content": "演示内容",
        "vector_text": "语义文本",
    }
    base.update(overrides)
    return WorldBookEntry.model_validate(base)


def _provider(**kwargs) -> _FakeProvider:
    return _FakeProvider(
        {
            "语义文本": [1.0, 0.0, 0.0],
            "同向查询": [1.0, 0.0, 0.0],
            "斜向查询": [1.0, 1.0, 0.0],   # 归一化后与「语义文本」余弦 ≈ 0.707
            "正交查询": [0.0, 1.0, 0.0],
        },
        **kwargs,
    )


# ---------- 索引构建 ----------


def test_no_provider_disables_channel() -> None:
    """未注入 provider 时索引为空，向量通道静默关闭。"""
    index = WorldBookVectorIndex([_entry()], None)
    assert index.indexed_count == 0
    assert index.match("同向查询") == set()


def test_entries_without_vector_text_not_indexed() -> None:
    """未声明 vector_text 的条目不入索引（只走关键词/正则）。"""
    index = WorldBookVectorIndex([_entry(vector_text="")], _provider())
    assert index.indexed_count == 0


def test_disabled_entries_still_indexed() -> None:
    """停用条目也入索引——是否采用由 matcher 决定，避免启停时重建索引。"""
    index = WorldBookVectorIndex([_entry(enabled=False)], _provider())
    assert index.indexed_count == 1


def test_encoding_failure_degrades_silently() -> None:
    """条目编码失败时整体降级为空索引，不抛异常（对话不中断）。"""
    index = WorldBookVectorIndex([_entry()], _provider(fail_on={"语义文本"}))
    assert index.indexed_count == 0
    assert index.match("同向查询") == set()


# ---------- 命中判定 ----------


def test_similarity_above_threshold_hits() -> None:
    """相似度 >= 阈值即命中（同向向量余弦为 1.0）。"""
    index = WorldBookVectorIndex([_entry()], _provider())
    assert index.match("同向查询") == {"demo"}


def test_similarity_below_threshold_misses() -> None:
    """相似度低于阈值不命中（正交向量余弦为 0.0）。"""
    index = WorldBookVectorIndex([_entry()], _provider())
    assert index.match("正交查询") == set()


def test_threshold_is_per_entry() -> None:
    """阈值可按条目覆盖：斜向查询余弦 ≈ 0.707。"""
    lenient = _entry(id="lenient", vector_threshold=0.6)
    strict = _entry(id="strict", vector_threshold=0.8)
    index = WorldBookVectorIndex([lenient, strict], _provider())
    assert index.match("斜向查询") == {"lenient"}


def test_default_threshold_is_0_7() -> None:
    """默认阈值 0.7（经 Qwen3-Embedding-8B 实测校准，见 models.py 说明）。"""
    assert _entry().vector_threshold == 0.7


def test_empty_text_returns_no_hit() -> None:
    """空输入不触发。"""
    index = WorldBookVectorIndex([_entry()], _provider())
    assert index.match("   ") == set()


def test_query_encoding_failure_returns_empty() -> None:
    """查询编码失败时返回空命中，不抛异常。"""
    index = WorldBookVectorIndex([_entry()], _provider(fail_on={"坏查询"}))
    assert index.match("坏查询") == set()


def test_dimension_mismatch_skips_entry() -> None:
    """查询与条目向量维度不一致时跳过该条目（如中途换了 embedding 模型）。"""
    provider = _FakeProvider(
        {"语义文本": [1.0, 0.0, 0.0], "同向查询": [1.0, 0.0, 0.0, 0.0]}
    )
    index = WorldBookVectorIndex([_entry()], provider)
    assert index.match("同向查询") == set()
