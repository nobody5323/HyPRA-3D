"""rag.retrieval：混合检索底座（稠密向量 + BM25 稀疏 → RRF 融合）。

- tokenize.py  中文分词（jieba；缺失时降级为字符 bigram）
- bm25.py      BM25 增量索引（支持增删，IDF 按当前语料计算）
- hybrid.py    RRF 融合（纯函数）

本子包被多处共用：**情景记忆**（温层）召回、**个人记忆**（知识库）召回、
**语义记忆**的冲突检测。它只负责「检索出什么」，不涉及注入编排——
分层拼接与 token 预算见 `app/rag/prompt_manager.py`。

注意：此处**不 re-export `tokenize` 函数**——那会把子模块 `tokenize` 的
名字覆盖成函数（`import app.rag.retrieval.tokenize` 会拿到函数而非模块）。
需用分词请写 `from app.rag.retrieval.tokenize import tokenize`。
"""

from app.rag.retrieval.bm25 import BM25Index
from app.rag.retrieval.hybrid import DEFAULT_RRF_K, reciprocal_rank_fusion
from app.rag.retrieval.tokenize import tokenizer_name

__all__ = [
    "DEFAULT_RRF_K",
    "BM25Index",
    "reciprocal_rank_fusion",
    "tokenizer_name",
]
