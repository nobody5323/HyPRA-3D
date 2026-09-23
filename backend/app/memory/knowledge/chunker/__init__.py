"""文档分块：递归字符切分 + 语义边界 + 句级 overlap 的融合。

拆分自原 `chunker.py`（`AGENTS.md §9.11` P2）：**「在哪里切」变成可替换策略**，
「怎么保证块长可控」是共用骨架（`base.py` 的模板方法）。

**对外接口保持不变**——

    from app.memory.knowledge.chunker import split_text

本模块是兼容层：既有调用方（`api/knowledge.py`）与测试都按旧路径 import，
重导出让这次拆分对它们完全透明。
"""

from app.memory.knowledge.chunker.base import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MIN_CHUNK,
    DEFAULT_OVERLAP,
    DEFAULT_SEMANTIC_THRESHOLD,
    DEFAULT_TARGET,
    Chunker,
)
from app.memory.knowledge.chunker.factory import (
    CHUNKERS,
    create_chunker,
    split_text,
)
from app.memory.knowledge.chunker.plain_chunker import PlainChunker
from app.memory.knowledge.chunker.semantic_chunker import SemanticChunker, semantic_boundaries

__all__ = [
    "CHUNKERS",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MIN_CHUNK",
    "DEFAULT_OVERLAP",
    "DEFAULT_SEMANTIC_THRESHOLD",
    "DEFAULT_TARGET",
    "Chunker",
    "PlainChunker",
    "SemanticChunker",
    "create_chunker",
    "semantic_boundaries",
    "split_text",
]
