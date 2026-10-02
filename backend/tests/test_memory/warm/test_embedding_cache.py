"""embedding 缓存包装测试（对话链路提速的关键一环）。

背景：一轮对话会对**同一句用户输入**发起 4–7 次编码请求（世界书向量通道 1 次、
知识库每个作用域各 1 次、温层情景记忆 1 次），且串行执行。远程 embedding
单次 200–500ms，不缓存就是白等 1–3 秒。这些测试锁住「同文本只编码一次」。
"""

from app.memory.warm.embedding import (
    CachedEmbeddingProvider,
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
)


class _CountingProvider(EmbeddingProvider):
    """记录真实调用次数的假 provider（用来断言缓存真的生效）。"""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim
        self.embed_calls = 0
        self.batch_calls = 0
        self.seen: list[str] = []
        #: 内层 provider 的专有属性，用于验证透传
        self.model = "fake-model"

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        self.seen.append(text)
        # 向量随文本变化，才能断言「不同文本拿到不同向量」
        base = float(sum(text.encode("utf-8")) % 97) / 97.0
        return [base] * self._dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return [self.embed(text) for text in texts]


def test_same_text_encoded_once() -> None:
    """★ 一轮内对同一句输入的多次编码，只应真正发出一次请求。"""
    inner = _CountingProvider()
    provider = CachedEmbeddingProvider(inner)

    for _ in range(4):
        provider.embed("我最近总是失眠")

    assert inner.embed_calls == 1
    assert provider.stats()["hits"] == 3


def test_different_texts_each_encoded() -> None:
    """不同文本不能被当成同一份（缓存键必须区分内容）。"""
    inner = _CountingProvider()
    provider = CachedEmbeddingProvider(inner)

    provider.embed("今天有点累")
    provider.embed("明天要早起")

    assert inner.embed_calls == 2


def test_cached_vector_is_identical_to_uncached() -> None:
    """缓存返回的必须是同一份向量，不能改变数值。"""
    inner = DeterministicEmbeddingProvider(dim=64)
    provider = CachedEmbeddingProvider(inner)
    text = "苏澄的咨询室在梧桐树下"

    first = provider.embed(text)
    second = provider.embed(text)

    assert first == second == inner.embed(text)


def test_lru_eviction_keeps_recent_entries() -> None:
    """超出容量时淘汰最久未使用的条目，容量内仍命中。"""
    inner = _CountingProvider()
    provider = CachedEmbeddingProvider(inner, max_entries=2)

    provider.embed("甲")
    provider.embed("乙")
    provider.embed("甲")  # 甲变成最近使用
    provider.embed("丙")  # 淘汰乙

    assert inner.embed_calls == 3
    provider.embed("甲")  # 仍在缓存
    assert inner.embed_calls == 3
    provider.embed("乙")  # 已被淘汰 → 重新编码
    assert inner.embed_calls == 4


def test_batch_reuses_cache_and_dedupes() -> None:
    """批量编码：批内去重 + 复用已有缓存，未命中项合并为一次内层批量请求。"""
    inner = _CountingProvider()
    provider = CachedEmbeddingProvider(inner)

    provider.embed("共享条目")  # 先放进缓存
    inner.batch_calls = 0
    inner.embed_calls = 0
    inner.seen.clear()

    vectors = provider.embed_batch(["共享条目", "新条目", "新条目", "另一条"])

    assert len(vectors) == 4
    # 三个不同文本里有一个命中缓存 → 只剩两个未命中交给内层
    assert inner.seen == ["新条目", "另一条"]
    assert inner.batch_calls == 1


def test_batch_preserves_input_order_and_duplicates() -> None:
    """返回顺序必须与入参严格一一对应（含重复项）。"""
    inner = _CountingProvider()
    provider = CachedEmbeddingProvider(inner)

    texts = ["甲", "乙", "甲", "丙", "乙"]
    vectors = provider.embed_batch(texts)

    assert len(vectors) == len(texts)
    assert vectors[0] == vectors[2]
    assert vectors[1] == vectors[4]
    assert vectors[0] != vectors[1]


def test_empty_batch_returns_empty() -> None:
    inner = _CountingProvider()
    provider = CachedEmbeddingProvider(inner)

    assert provider.embed_batch([]) == []
    assert inner.batch_calls == 0


def test_dimension_and_unknown_attributes_pass_through() -> None:
    """包装后上层仍要能读到内层的维度与配置属性（否则静默拿到默认值）。"""
    inner = _CountingProvider(dim=16)
    provider = CachedEmbeddingProvider(inner)

    assert provider.dimension == 16
    assert provider.model == "fake-model"
    assert provider.inner is inner


def test_missing_attribute_raises_attribute_error() -> None:
    """内层也没有的属性应正常抛 AttributeError，而不是递归崩溃。"""
    provider = CachedEmbeddingProvider(_CountingProvider())

    try:
        provider.definitely_not_a_real_attribute
    except AttributeError:
        return
    raise AssertionError("应当抛出 AttributeError")
