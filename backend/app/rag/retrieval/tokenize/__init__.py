"""中文分词（BM25 稀疏检索通道的入口）。

拆分自原 `tokenize.py`（`AGENTS.md §9.11` P2）：对外接口**保持不变**——

    from app.rag.retrieval.tokenize import tokenize, tokenizer_name

实现见 `jieba_tokenizer.py`（主）与 `bigram_tokenizer.py`（零依赖降级），
由 `factory.py` 按 `tokenizer_backend` 选择。

**本模块是兼容层**：既有调用方（`bm25.py`、`app.rag.retrieval`）与测试都按旧路径
import，重导出让这次拆分对它们完全透明。
"""

from app.rag.retrieval.tokenize.base import STOP_WORDS, Tokenizer, clean_tokens, has_content
from app.rag.retrieval.tokenize.bigram_tokenizer import BigramTokenizer
from app.rag.retrieval.tokenize.factory import (
    BACKENDS,
    create_tokenizer,
    default_tokenizer,
    tokenize,
    tokenizer_name,
)
from app.rag.retrieval.tokenize.jieba_tokenizer import JiebaTokenizer, jieba_available

__all__ = [
    "BACKENDS",
    "BigramTokenizer",
    "JiebaTokenizer",
    "STOP_WORDS",
    "Tokenizer",
    "clean_tokens",
    "create_tokenizer",
    "default_tokenizer",
    "has_content",
    "jieba_available",
    "tokenize",
    "tokenizer_name",
]
