"""分块器工厂：按名字或「有没有 embedding」选择实现。

**不引入新的配置开关**：`KNOWLEDGE_SEMANTIC_CHUNKING` 已经表达了「要不要语义切分」，
再加一个 `KNOWLEDGE_CHUNKER` 就是两个开关说同一件事——迟早漂移成
「一个开一个关」的谜题。工厂的按名创建是给**能力注册表与第三方**用的，
既有调用方继续用 `split_text(embed=...)` 的语义。
"""

from __future__ import annotations

from collections.abc import Callable

from app.memory.knowledge.chunker.base import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MIN_CHUNK,
    DEFAULT_OVERLAP,
    DEFAULT_SEMANTIC_THRESHOLD,
    DEFAULT_TARGET,
    Chunker,
)
from app.memory.knowledge.chunker.plain_chunker import PlainChunker
from app.memory.knowledge.chunker.semantic_chunker import SemanticChunker

AUTO = "auto"
SEMANTIC = "semantic"
PLAIN = "plain"

#: 可选值（供报错信息与文档引用）
CHUNKERS: tuple[str, ...] = (AUTO, SEMANTIC, PLAIN)

#: plain 的常见别名
_PLAIN_ALIASES = frozenset({"plain", "recursive", "recursive-character", "fixed"})


def create_chunker(
    name: str = "",
    *,
    embed: Callable[[list[str]], list[list[float]]] | None = None,
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
) -> Chunker:
    """按名字创建分块器。

    `name` 为空或 `auto` 时**由 embed 决定**：给了 embed 用语义边界，否则纯递归
    ——与既有 `split_text()` 的降级语义一致。

    `semantic` 但没给 embed → 抛错（显式要求的能力不静默降级）。

    抛出:
        ValueError: 名字未知，或要求 semantic 却没给 embed。
    """
    key = (name or "").strip().lower()

    if key in {"", AUTO}:
        return (
            SemanticChunker(embed, threshold=semantic_threshold)
            if embed is not None
            else PlainChunker()
        )
    if key == SEMANTIC:
        return SemanticChunker(embed, threshold=semantic_threshold)
    if key in _PLAIN_ALIASES:
        return PlainChunker()
    raise ValueError(f"未知分块器：{name!r}（可选 {' | '.join(CHUNKERS)}）")


def split_text(
    text: str,
    *,
    target: int = DEFAULT_TARGET,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    min_chunk: int = DEFAULT_MIN_CHUNK,
    embed: Callable[[list[str]], list[list[float]]] | None = None,
    semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
) -> list[str]:
    """把清洗后的文本切成适合入库与召回的分块（**既有入口，签名与语义不变**）。

    参数:
        text: 已清洗的文本（见 cleaner.py）；
        target / max_chars / overlap / min_chunk: 分块长度参数（字符）；
        embed: 批量编码函数；提供时启用语义边界判据，为 None 则退化为纯递归切分；
        semantic_threshold: 相邻单元余弦低于此值视为语义低谷。

    返回:
        分块列表（按原文顺序）；空文本返回空列表。
    """
    chunker = create_chunker("auto", embed=embed, semantic_threshold=semantic_threshold)
    return chunker.split(
        text,
        target=target,
        max_chars=max_chars,
        overlap=overlap,
        min_chunk=min_chunk,
    )
