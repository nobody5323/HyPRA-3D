"""个人记忆的内存实现（离线开发 / 测试 / 演示）。

与 InMemoryWarmStore 同一思路：进程内 dict + 确定性 embedding，
重启即清空。接口与 Qdrant 实现完全一致，可直接替换。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.memory.knowledge.base import (
    KnowledgeChunk,
    KnowledgeDoc,
    KnowledgeHit,
    KnowledgeStore,
)
from app.memory.warm.embedding import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    cosine_similarity,
)


class InMemoryKnowledgeStore(KnowledgeStore):
    """进程内实现（重启即清空）。"""

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self._provider = provider or DeterministicEmbeddingProvider()
        # companion_id -> doc_id -> KnowledgeDoc
        self._docs: dict[str, dict[str, KnowledgeDoc]] = {}
        # companion_id -> chunk_id -> KnowledgeChunk
        self._chunks: dict[str, dict[str, KnowledgeChunk]] = {}

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    # ---------- 内部 ----------

    def _docs_of(self, companion_id: str) -> dict[str, KnowledgeDoc]:
        return self._docs.setdefault(companion_id, {})

    def _chunks_of(self, companion_id: str) -> dict[str, KnowledgeChunk]:
        return self._chunks.setdefault(companion_id, {})

    # ---------- 接口实现 ----------

    def add_document(
        self,
        companion_id: str,
        *,
        title: str,
        chunks: list[str],
        source_type: str = "text",
        doc_md5: str = "",
        content_md5: str = "",
        minhash: tuple[int, ...] = (),
        doc_id: str | None = None,
    ) -> KnowledgeDoc:
        resolved_id = doc_id or uuid.uuid4().hex
        # 覆盖语义：先清掉同名文档的旧分块，避免孤儿分块
        self.delete_document(companion_id, resolved_id)

        created = datetime.now()
        target = self._chunks_of(companion_id)
        for index, text in enumerate(chunks):
            chunk_id = f"{resolved_id}::{index}"
            target[chunk_id] = KnowledgeChunk(
                chunk_id=chunk_id,
                doc_id=resolved_id,
                companion_id=companion_id,
                text=text,
                index=index,
                created_at=created,
                vector=self._provider.embed(text),
            )

        doc = KnowledgeDoc(
            doc_id=resolved_id,
            companion_id=companion_id,
            title=title,
            source_type=source_type,
            chunk_count=len(chunks),
            created_at=created,
            doc_md5=doc_md5,
            content_md5=content_md5,
            minhash=tuple(minhash),
        )
        self._docs_of(companion_id)[resolved_id] = doc
        return doc

    def search(
        self, companion_id: str, query: str, *, top_k: int = 5
    ) -> list[KnowledgeHit]:
        if top_k <= 0:
            return []
        chunks = self._chunks_of(companion_id)
        if not chunks:
            return []

        query_vector = self._provider.embed(query)
        hits = [
            KnowledgeHit(
                chunk=chunk,
                score=similarity,
                raw_similarity=similarity,
            )
            for chunk in chunks.values()
            for similarity in [cosine_similarity(query_vector, chunk.vector)]
        ]
        # 同分时按 chunk_id 稳定排序，保证结果可复现
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
        return hits[:top_k]

    def list_documents(self, companion_id: str) -> list[KnowledgeDoc]:
        return sorted(
            self._docs_of(companion_id).values(),
            key=lambda doc: doc.created_at,
            reverse=True,
        )

    def get_document(self, companion_id: str, doc_id: str) -> KnowledgeDoc | None:
        return self._docs_of(companion_id).get(doc_id)

    def delete_document(self, companion_id: str, doc_id: str) -> int:
        removed = [
            chunk_id
            for chunk_id, chunk in self._chunks_of(companion_id).items()
            if chunk.doc_id == doc_id
        ]
        for chunk_id in removed:
            del self._chunks_of(companion_id)[chunk_id]
        self._docs_of(companion_id).pop(doc_id, None)
        return len(removed)

    def list_chunks(self, companion_id: str) -> list[KnowledgeChunk]:
        return sorted(
            self._chunks_of(companion_id).values(),
            key=lambda chunk: (chunk.doc_id, chunk.index),
        )

    def count_chunks(self, companion_id: str) -> int:
        return len(self._chunks_of(companion_id))

    def clear_scope(self, companion_id: str) -> int:
        """清空该陪伴对象的全部文档与分块，返回被删除的分块数。"""
        removed = len(self._chunks.pop(companion_id, {}))
        self._docs.pop(companion_id, None)
        return removed
