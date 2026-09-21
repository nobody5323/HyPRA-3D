"""知识库混合检索：BM25 + 稠密向量 → RRF 融合。

与情景记忆（`MemoryStore._recall_episodic`）**共用同一套检索底座**
（`app/rag/retrieval/`），但有三处刻意差异：

| | 情景记忆 | 个人记忆（本模块） |
|---|---|---|
| 时间衰减 | 有（30 天半衰期） | **无**——知识不老化 |
| 情绪加权 | 有 | **无**——知识是客观的 |
| 缓存失效 | 写入时同步 | **chunk 数变化时自动重建** |

最后一行的差异来自写入路径不同：情景记忆由对话链路写入（能直接拿到 store
实例并同步索引），而知识库由 API 路由写入（可能和检索器不在同一次调用里），
因此改用「chunk 数变化即重建」这种**自检式**失效，避免依赖显式通知。
"""

from __future__ import annotations

from app.memory.knowledge.base import KnowledgeChunk, KnowledgeHit, KnowledgeStore
from app.rag.retrieval.bm25 import BM25Index
from app.rag.retrieval.hybrid import reciprocal_rank_fusion

#: 最终返回条数
DEFAULT_TOP_K = 3
#: 各通道候选数（须远大于最终条数，否则 RRF 退化为合并去重）
DEFAULT_CANDIDATE_N = 10
#: RRF 平滑常数
DEFAULT_RRF_K = 60


class KnowledgeRetriever:
    """个人记忆的混合检索器（进程内缓存稀疏索引）。"""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        top_k: int = DEFAULT_TOP_K,
        candidate_n: int = DEFAULT_CANDIDATE_N,
        rrf_k: int = DEFAULT_RRF_K,
        min_similarity: float = 0.0,
    ) -> None:
        self.store = store
        self.top_k = top_k
        self.candidate_n = candidate_n
        self.rrf_k = rrf_k
        self.min_similarity = min_similarity
        # companion_id -> (缓存的 chunk 数, BM25 索引, chunk_id -> chunk)
        self._views: dict[str, tuple[int, BM25Index, dict[str, KnowledgeChunk]]] = {}

    # ---------- 内部 ----------

    def _lexical_view(
        self, companion_id: str
    ) -> tuple[BM25Index, dict[str, KnowledgeChunk]]:
        """惰性构建稀疏索引；chunk 数变化（上传或删除）时自动重建。"""
        count = self.store.count_chunks(companion_id)
        cached = self._views.get(companion_id)
        if cached is not None and cached[0] == count:
            return cached[1], cached[2]

        chunks = self.store.list_chunks(companion_id)
        index = BM25Index()
        for chunk in chunks:
            index.add(chunk.chunk_id, chunk.text)
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        self._views[companion_id] = (count, index, by_id)
        return index, by_id

    def invalidate(self, companion_id: str | None = None) -> None:
        """强制失效缓存（chunk 数未变但内容变了时使用，如覆盖写同长度文档）。"""
        if companion_id is None:
            self._views.clear()
        else:
            self._views.pop(companion_id, None)

    # ---------- 检索 ----------

    def retrieve(
        self, companion_id: str, query: str, *, top_k: int | None = None
    ) -> list[KnowledgeHit]:
        """混合检索，返回融合后的前 top_k 条。"""
        limit = top_k or self.top_k
        if limit <= 0 or not query.strip():
            return []

        # ① 稠密通道：阈值在**融合前**生效（RRF 分数只有相对含义，无法事后设阈）
        dense = self.store.search(companion_id, query, top_k=self.candidate_n)
        dense = [hit for hit in dense if hit.raw_similarity >= self.min_similarity]

        rankings: list[list[str]] = [[hit.chunk.chunk_id for hit in dense]]
        by_id: dict[str, KnowledgeHit] = {hit.chunk.chunk_id: hit for hit in dense}

        # ② 稀疏通道：BM25 能召回向量漏掉的文档（专有名词、编号等字面匹配）
        try:
            index, chunks = self._lexical_view(companion_id)
            sparse = index.search(query, top_k=self.candidate_n)
        except Exception:  # noqa: BLE001 - 稀疏通道不可用不应影响稠密召回
            sparse = []

        if sparse:
            rankings.append([chunk_id for chunk_id, _ in sparse])
            for chunk_id, _score in sparse:
                if chunk_id in by_id:
                    continue
                chunk = chunks.get(chunk_id)
                if chunk is not None:
                    by_id[chunk_id] = KnowledgeHit(
                        chunk=chunk, score=0.0, raw_similarity=0.0
                    )

        # ③ RRF 融合（只传稠密一路时，分数即 1/(k+rank)，保留相似度排序）
        fused = reciprocal_rank_fusion(rankings, k=self.rrf_k)

        results: list[KnowledgeHit] = []
        for chunk_id, score in fused:
            hit = by_id.get(chunk_id)
            if hit is None:
                continue
            results.append(
                KnowledgeHit(
                    chunk=hit.chunk, score=score, raw_similarity=hit.raw_similarity
                )
            )
        return results[:limit]
