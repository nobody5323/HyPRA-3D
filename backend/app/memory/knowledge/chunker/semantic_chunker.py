"""语义边界分块：在话题转折处提前切。

判据是**相邻单元的余弦相似度**：低于阈值说明话题在此转折，即便长度还没到 target
也适合断开。长度硬约束仍然由骨架负责，本策略只回答「哪里可以提前切」。

代价要说清楚：需要一次批量 embedding，且切点**不可复现**——换 embedding 模型会得到
不同的分块，缓存与增量索引会整体失配。所以它不是无条件默认：
`create_chunker()` 只在调用方**给了 embed** 时才选它。
"""

from __future__ import annotations

from collections.abc import Callable

from app.memory.knowledge.chunker.base import DEFAULT_SEMANTIC_THRESHOLD, Chunker
from app.memory.warm.embedding import cosine_similarity


def semantic_boundaries(
    units: list[str],
    embed: Callable[[list[str]], list[list[float]]],
    threshold: float,
) -> set[int]:
    """找出语义低谷位置。

    返回的整数 i 表示「可以在 units[i] **之前**切分」。
    编码结果与输入不等长时返回空集（宁可退化为纯长度切分，也不要按错位的向量切）。
    """
    if len(units) < 2:
        return set()

    vectors = embed(units)
    if len(vectors) != len(units):
        return set()

    return {
        index + 1
        for index in range(len(units) - 1)
        if cosine_similarity(vectors[index], vectors[index + 1]) < threshold
    }


class SemanticChunker(Chunker):
    """按相邻单元相似度决定切点。"""

    name = "semantic"

    def __init__(
        self,
        embed: Callable[[list[str]], list[list[float]]] | None,
        *,
        threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    ) -> None:
        if embed is None:
            # 不静默降级：显式要求语义切分却没给编码函数，说明装配有问题，
            # 悄悄退成纯递归只会让分块质量莫名变差（与 tokenizer 的强制模式同理）
            raise ValueError(
                "SemanticChunker 需要 embed 函数；没有 embedding 时请用 PlainChunker"
            )
        self._embed = embed
        self._threshold = threshold

    def boundaries(self, units: list[str]) -> set[int]:
        return semantic_boundaries(units, self._embed, self._threshold)
