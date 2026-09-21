"""温层内存实现（离线开发/测试用）。

基于 DeterministicEmbeddingProvider 与内存 dict；按 companion_id 隔离。
接 Qdrant 时实现 WarmMemoryStore 同一接口即可无缝替换。
"""

import uuid
from datetime import datetime

from app.memory.warm.base import (
    CANDIDATE_FACTOR,
    MemoryRecord,
    SearchResult,
    WarmMemoryStore,
)
from app.memory.warm.decay import combined_score
from app.memory.warm.embedding import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    cosine_similarity,
)


class InMemoryWarmStore(WarmMemoryStore):
    """进程内温层实现（重启即清空，仅供开发/测试/演示）。"""

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self._provider = provider or DeterministicEmbeddingProvider()
        # companion_id -> {memory_id: MemoryRecord}
        self._collections: dict[str, dict[str, MemoryRecord]] = {}

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    # ---------- 内部 ----------

    def _collection(self, companion_id: str) -> dict[str, MemoryRecord]:
        return self._collections.setdefault(companion_id, {})

    def _age_days(self, created_at: datetime, now: datetime) -> float:
        return max(0.0, (now - created_at).total_seconds() / 86400.0)

    # ---------- 接口实现 ----------

    def add(
        self,
        companion_id: str,
        text: str,
        *,
        metadata: dict | None = None,
        memory_id: str | None = None,
        created_at: datetime | None = None,
    ) -> str:
        mid = memory_id or uuid.uuid4().hex
        record = MemoryRecord(
            memory_id=mid,
            companion_id=companion_id,
            text=text,
            created_at=created_at or datetime.now(),
            metadata=dict(metadata or {}),
            vector=self._provider.embed(text),
        )
        self._collection(companion_id)[mid] = record
        return mid

    def search(
        self,
        companion_id: str,
        query: str,
        *,
        top_k: int = 5,
        half_life_days: float = 30.0,
        decay_exponent: float = 1.0,
        now: datetime | None = None,
    ) -> list[SearchResult]:
        now = now or datetime.now()
        query_vec = self._provider.embed(query)

        # ① 先按**原始相似度**取候选（与 QdrantWarmStore 保持同一策略：
        #    时间衰减只在候选内重排，若先衰减再截断会让排序失真）
        scored = [
            (cosine_similarity(query_vec, record.vector), record)
            for record in self._collection(companion_id).values()
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        candidates = scored[: max(top_k * CANDIDATE_FACTOR, top_k)]

        # ② 对候选施加时间衰减后重排
        results: list[SearchResult] = [
            SearchResult(
                record=record,
                score=combined_score(
                    similarity,
                    self._age_days(record.created_at, now),
                    half_life_days=half_life_days,
                    decay_exponent=decay_exponent,
                ),
                raw_similarity=similarity,
            )
            for similarity, record in candidates
        ]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def delete(self, companion_id: str, memory_id: str) -> bool:
        collection = self._collection(companion_id)
        return collection.pop(memory_id, None) is not None

    def count(self, companion_id: str) -> int:
        return len(self._collection(companion_id))

    def list_records(self, companion_id: str) -> list[MemoryRecord]:
        return list(self._collection(companion_id).values())
