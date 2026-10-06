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

作用域口径见 `app/memory/knowledge/scopes`（检索侧只调它，不自己拼键）：
基础两个作用域（本陪伴对象 + 共享 `*`）两种模式都查；酒馆世界书那几本
（每本一个 `tavern:book:{哈希}` 作用域）**只在酒馆聊天模式、且只对「当前已挂载」
的书**查——挂载清单由插件配置给出，默认空（一本都不挂）。
各路候选合并后重新 RRF，而不是拼几段结果。

**通道策略按来源分档**（`_needs_sparse`）：用户上传语料与共享作用域保持
BM25 + 向量的完整混合；**酒馆世界书来源改用「稠密优先」**——稠密候选够用就不跑 BM25。
中文 BM25 只认字面，对「衣服」这类泛词会在多个条目上同时命中，RRF 融合后把语义更
相关的条目挤出 top_k（实测：问「她平时喜欢穿什么衣服？」时「凌颜玉的衣柜」被三个
NPC 条目挤掉；同一评测集纯向量 8/8、等权混合 7/8，加权混合也救不回——要让衣柜
反超需要把稠密权重放大到 64 倍以上，等于关掉 BM25）。稠密候选不足时才用 BM25 兜底，
保住它对专有名词/编号的召回价值。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.memory.knowledge.base import KnowledgeChunk, KnowledgeHit, KnowledgeStore
from app.memory.knowledge.scopes import is_tavern_scope, retrieval_scopes
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

    def _needs_sparse(self, scope: str, dense_count: int, limit: int) -> bool:
        """该作用域是否还要跑稀疏（BM25）通道。

        分档规则（**只有酒馆世界书来源变了，其余一律保持原混合策略**）：

        - 非酒馆来源（用户上传语料 / 共享作用域）→ 恒为 True，行为与改动前完全一致；
        - 酒馆来源 → **稠密优先**：稠密候选已经够本轮返回（`>= limit`）就不再跑 BM25。

        为什么对酒馆来源单独收紧：中文 BM25 只认字面，而世界书条目里
        「衣服 / 角色 / 设定」这类泛词遍地都是，一次 query 能同时命中好几条 NPC 设定；
        RRF 是等权排名融合，这些条目拿到稀疏通道的高名次后会把**语义更相关、
        但字面没命中**的条目挤出 top_k。实测：问「她平时喜欢穿什么衣服？」时
        「凌颜玉的衣柜」（稠密第 2）被「柳青 / 白婉晴 / 丝袜特化」挤掉——
        那三条只是在正文里出现过「衣服」二字。加权融合也救不回：要让衣柜反超，
        稠密权重要放大到 64 倍以上，等于变相关掉 BM25。

        稠密候选不足时（语料太少，或阈值过滤后剩不下几条）仍走 BM25 兜底，
        保住它对专有名词、编号等字面精确匹配的召回价值。
        """
        if not is_tavern_scope(scope):
            return True
        return dense_count < limit

    def invalidate(self, companion_id: str | None = None) -> None:
        """强制失效缓存（chunk 数未变但内容变了时使用，如覆盖写同长度文档）。"""
        if companion_id is None:
            self._views.clear()
        else:
            self._views.pop(companion_id, None)

    # ---------- 检索 ----------

    def retrieve(
        self,
        companion_id: str,
        query: str,
        *,
        top_k: int | None = None,
        include_tavern: bool = False,
        mounted_books: Iterable[str] = (),
    ) -> list[KnowledgeHit]:
        """混合检索，返回融合后的前 top_k 条。

        作用域由 `scopes.retrieval_scopes` 统一给出，两种模式不同：

        - **基础**（两种模式都查）：本陪伴对象 + 共享作用域（`SCOPE_ALL`）
          —— 用户上传的语料按对象隔离，共享的那份只存一次；
        - **酒馆世界书**（`include_tavern=True`，仅酒馆聊天模式）：**只查当前挂载的
          那几本**（`mounted_books` 给出来源标识，每本换成一个 `tavern:book:{哈希}`
          作用域）。**默认一本都不挂 = 一本都不查**，因此不想用的世界书绝不会
          被串味召回；桌宠模式（`include_tavern=False`）则一个世界书作用域都不查。

        各路候选合并后**重新融合**，而不是拼几段结果——拼的话「谁更相关」就变成
        「谁的通道先被遍历」。
        """
        limit = top_k or self.top_k
        if limit <= 0 or not query.strip():
            return []

        scopes = retrieval_scopes(
            companion_id, include_tavern=include_tavern, mounted_books=mounted_books
        )

        candidates: dict[str, KnowledgeHit] = {}
        rankings: list[list[str]] = []

        for scope in scopes:
            # ① 稠密通道：阈值在**融合前**生效（RRF 分数只有相对含义，无法事后设阈）
            dense = [
                hit
                for hit in self.store.search(scope, query, top_k=self.candidate_n)
                if hit.raw_similarity >= self.min_similarity
            ]
            if dense:
                rankings.append([hit.chunk.chunk_id for hit in dense])
                for hit in dense:
                    candidates.setdefault(hit.chunk.chunk_id, hit)

            # ② 稀疏通道：BM25 能召回向量漏掉的文档（专有名词、编号等字面匹配）。
            #    酒馆来源在稠密候选已够用时**跳过**这一步，避免泛词的字面命中
            #    在 RRF 里压过语义相关的条目（见 `_needs_sparse`）。
            if not self._needs_sparse(scope, len(dense), limit):
                continue
            try:
                index, chunks = self._lexical_view(scope)
                sparse = index.search(query, top_k=self.candidate_n)
            except Exception:  # noqa: BLE001 - 稀疏通道不可用不应影响稠密召回
                sparse, chunks = [], {}

            if sparse:
                rankings.append([chunk_id for chunk_id, _ in sparse])
                for chunk_id, _score in sparse:
                    if chunk_id in candidates:
                        continue
                    chunk = chunks.get(chunk_id)
                    if chunk is not None:
                        candidates[chunk_id] = KnowledgeHit(
                            chunk=chunk, score=0.0, raw_similarity=0.0
                        )

        if not rankings:
            return []

        # ③ RRF 融合（只传一路时，分数即 1/(k+rank)，保留相似度排序）
        fused = reciprocal_rank_fusion(rankings, k=self.rrf_k)

        results: list[KnowledgeHit] = []
        for chunk_id, score in fused:
            hit = candidates.get(chunk_id)
            if hit is None:
                continue
            results.append(
                KnowledgeHit(
                    chunk=hit.chunk, score=score, raw_similarity=hit.raw_similarity
                )
            )
        return results[:limit]
