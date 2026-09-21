"""温层 Qdrant 实现：云 / 本地 Docker / 本地嵌入式三模式同一份代码。

连接方式（构造参数选一）：
- local_path=":memory:"  纯内存（测试用，无需服务）
- local_path="<目录>"     本地嵌入式持久化（无 Docker 也能开发）
- url=... (+ api_key)     远程服务（评审用 docker 内网 / 开发用云 Qdrant）

实现要点：
- collection 隔离：每个陪伴对象一个 memory_{companion_id}（参照⑧）；
- 时间衰减：Qdrant 只负责相似度召回，应用层再用 combined_score 重排
  （保持 decay 逻辑单一来源，且支持时间衰减参数调节）；
- id 约束：Qdrant 要求 point id 为 UUID 或整数，故 memory_id 用 uuid4().hex。
"""

from datetime import datetime

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.memory.warm.base import (
    CANDIDATE_FACTOR,
    MemoryRecord,
    SearchResult,
    WarmMemoryStore,
)
from app.memory.warm.decay import combined_score
from app.memory.warm.embedding import EmbeddingProvider, create_embedding_provider

# collection 名前缀（按陪伴对象隔离）
COLLECTION_PREFIX = "memory_"


class QdrantWarmStore(WarmMemoryStore):
    """基于 Qdrant 的温层实现。"""

    def __init__(
        self,
        provider: EmbeddingProvider | None = None,
        *,
        url: str | None = None,
        api_key: str | None = None,
        local_path: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        if local_path:
            # ":memory:" 纯内存；其他值为本地持久化目录
            self._client = QdrantClient(location=local_path)
        elif url:
            self._client = QdrantClient(url=url, api_key=api_key or None, timeout=timeout)
        else:
            raise ValueError("必须提供 local_path 或 url 之一")

        self._provider = provider or create_embedding_provider("deterministic")

    # ---------- 内部 ----------

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    def _collection_name(self, companion_id: str) -> str:
        return f"{COLLECTION_PREFIX}{companion_id}"

    def _ensure_collection(self, companion_id: str) -> str:
        """collection 不存在则创建（按当前 embedding 维度、余弦距离）。"""
        name = self._collection_name(companion_id)
        if not self._client.collection_exists(name):
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self._provider.dimension,
                    distance=Distance.COSINE,
                ),
            )
        return name

    @staticmethod
    def _payload_to_record(
        point_id: str,
        payload: dict | None,
        companion_id: str,
        vector: list[float] | None = None,
    ) -> MemoryRecord:
        payload = payload or {}
        created_raw = payload.get("created_at")
        created_at = datetime.fromisoformat(created_raw) if created_raw else datetime.now()
        return MemoryRecord(
            memory_id=str(point_id),
            companion_id=companion_id,
            text=payload.get("text", ""),
            created_at=created_at,
            metadata=payload.get("metadata", {}) or {},
            vector=vector or [],
        )

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
        """入库一条记忆。created_at 可选（缺省为当前时间）。"""
        import uuid

        name = self._ensure_collection(companion_id)
        mid = memory_id or uuid.uuid4().hex
        created = created_at or datetime.now()
        self._client.upsert(
            collection_name=name,
            points=[
                PointStruct(
                    id=mid,
                    vector=self._provider.embed(text),
                    payload={
                        "text": text,
                        "created_at": created.isoformat(),
                        "metadata": dict(metadata or {}),
                    },
                )
            ],
        )
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
        name = self._ensure_collection(companion_id)
        now = now or datetime.now()

        # ① Qdrant 召回更多候选（相似度排序）
        response = self._client.query_points(
            collection_name=name,
            query=self._provider.embed(query),
            limit=max(top_k * CANDIDATE_FACTOR, top_k),
            with_payload=True,
        )

        # ② 应用层做时间衰减重排（参照⑥）
        results: list[SearchResult] = []
        for point in response.points:
            record = self._payload_to_record(point.id, point.payload, companion_id)
            age_days = max(0.0, (now - record.created_at).total_seconds() / 86400.0)
            score = combined_score(
                float(point.score),
                age_days,
                half_life_days=half_life_days,
                decay_exponent=decay_exponent,
            )
            results.append(
                SearchResult(record=record, score=score, raw_similarity=float(point.score))
            )
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    def delete(self, companion_id: str, memory_id: str) -> bool:
        name = self._collection_name(companion_id)
        if not self._client.collection_exists(name):
            return False
        self._client.delete(collection_name=name, points_selector=[memory_id])
        # 校验是否真的删除（Qdrant delete 不返回是否命中）
        remaining = self._client.retrieve(collection_name=name, ids=[memory_id])
        return len(remaining) == 0

    def count(self, companion_id: str) -> int:
        name = self._collection_name(companion_id)
        if not self._client.collection_exists(name):
            return 0
        return self._client.count(collection_name=name).count

    def list_records(self, companion_id: str) -> list[MemoryRecord]:
        """滚动拉取全量记录（供构建 BM25 稀疏索引）。

        Qdrant 只提供向量与 payload，无法在其内部做 BM25，因此需要把文本
        拉回应用层建镜像索引。上层会缓存索引，本方法仅在首次召回时调用。
        """
        name = self._collection_name(companion_id)
        if not self._client.collection_exists(name):
            return []

        records: list[MemoryRecord] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=name,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            records.extend(
                self._payload_to_record(point.id, point.payload, companion_id)
                for point in points
            )
            if offset is None:
                break
        return records
