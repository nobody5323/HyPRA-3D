"""BM25 稀疏检索索引（增量可更新）。

与稠密向量检索组成混合检索（RRF 融合），补上向量检索的两块短板：

1. **字面精确匹配**：人名、专有名词、编号等场景向量容易语义漂移，BM25 稳定；
2. **不受时间衰减影响**：老记忆只要关键词命中就能被重新召回，从而刷新其
   `last_recalled_at`，避免被淘汰机制误杀（衰减与淘汰的正反馈）。

为什么自己实现而不用 `rank_bm25`：本项目的语料是**增量增长**的（每轮对话写入
一条），而 `rank_bm25.BM25Okapi` 在构造时一次性算好 IDF，新增文档需重建整个索引。
此处维护文档频次 `df`，**查询时按当前语料计算 IDF**，支持增删。

规模假设：单陪伴对象的记忆条数为百量级，故用简单的倒排表 + 全量打分，
不需要压缩索引或近似最近邻。
"""

from __future__ import annotations

import math
from collections import Counter

from app.rag.retrieval.tokenize import tokenize

#: BM25 标准参数：k1 控制词频饱和速度，b 控制文档长度归一化强度
DEFAULT_K1 = 1.5
DEFAULT_B = 0.75


class BM25Index:
    """可增删的 BM25 索引（doc_id → 文本）。"""

    def __init__(self, *, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> None:
        self.k1 = k1
        self.b = b
        self._term_freq: dict[str, Counter[str]] = {}   # doc_id → 词频表
        self._length: dict[str, int] = {}               # doc_id → 词数
        self._postings: dict[str, set[str]] = {}        # term → 含该词的 doc_id 集合
        self._total_length = 0

    # ---------- 只读属性 ----------

    def __len__(self) -> int:
        return len(self._term_freq)

    def __contains__(self, doc_id: str) -> bool:
        return doc_id in self._term_freq

    @property
    def avg_length(self) -> float:
        """平均文档长度（BM25 长度归一化用）。"""
        return self._total_length / len(self._term_freq) if self._term_freq else 0.0

    def doc_ids(self) -> set[str]:
        """当前索引内的全部 doc_id。"""
        return set(self._term_freq)

    # ---------- 增删 ----------

    def add(self, doc_id: str, text: str) -> None:
        """加入（或替换）一个文档；切不出 token 的文本不入索引。"""
        if doc_id in self._term_freq:
            self.remove(doc_id)
        tokens = tokenize(text)
        if not tokens:
            return

        counts = Counter(tokens)
        self._term_freq[doc_id] = counts
        self._length[doc_id] = len(tokens)
        self._total_length += len(tokens)
        for term in counts:
            self._postings.setdefault(term, set()).add(doc_id)

    def remove(self, doc_id: str) -> bool:
        """移除一个文档，返回它是否原本存在。"""
        counts = self._term_freq.pop(doc_id, None)
        if counts is None:
            return False
        self._total_length -= self._length.pop(doc_id, 0)
        for term in counts:
            postings = self._postings.get(term)
            if postings is None:
                continue
            postings.discard(doc_id)
            if not postings:
                del self._postings[term]
        return True

    def clear(self) -> None:
        """清空索引（切换索引来源时使用）。"""
        self._term_freq.clear()
        self._length.clear()
        self._postings.clear()
        self._total_length = 0

    # ---------- 检索 ----------

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """返回 [(doc_id, BM25 分)]，按分数降序。

        只返回**至少命中一个查询词**的文档；无命中时返回空列表
        （与向量检索不同，这里是稀疏通道，宁缺毋滥）。
        """
        if top_k <= 0 or not self._term_freq:
            return []
        query_terms = set(tokenize(query))
        if not query_terms:
            return []

        total_docs = len(self._term_freq)
        avg_len = self.avg_length or 1.0
        scores: dict[str, float] = {}

        for term in query_terms:
            postings = self._postings.get(term)
            if not postings:
                continue
            df = len(postings)
            # BM25 概率型 IDF（加 1 保证非负，避免高频词得负分）
            idf = math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
            for doc_id in postings:
                tf = self._term_freq[doc_id][term]
                length = self._length[doc_id]
                denominator = tf + self.k1 * (1.0 - self.b + self.b * length / avg_len)
                scores[doc_id] = (
                    scores.get(doc_id, 0.0)
                    + idf * tf * (self.k1 + 1.0) / denominator
                )

        # 同分时按 doc_id 稳定排序，保证结果可复现（便于测试与调试）
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k]
