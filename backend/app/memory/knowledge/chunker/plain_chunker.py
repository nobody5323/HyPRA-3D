"""纯递归分块：只看长度与标点，不看语义。

零依赖（不需要 embedding），切点**可复现**——同样的文本永远得到同样的分块。

两个真实用途：
1. 没有配 embedding 的部署（降级路径）；
2. 需要分块稳定的场合——语义切点会随 embedding 模型改变而变化，
   换了模型就得到不同的分块，缓存或增量索引会整体失配。
"""

from __future__ import annotations

from app.memory.knowledge.chunker.base import Chunker


class PlainChunker(Chunker):
    """四级递归降级 + 长度聚合，不做语义判断。"""

    name = "plain"

    def boundaries(self, units: list[str]) -> set[int]:  # noqa: ARG002 - 接口约定需要该参数
        """没有语义低谷：切点完全由 `pack()` 的长度规则决定。"""
        return set()
