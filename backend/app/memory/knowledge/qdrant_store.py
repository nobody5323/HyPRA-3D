"""个人记忆的 Qdrant 实现（云 / 本地 Docker / 本地嵌入式同一份代码）。

每个陪伴对象使用**两个 collection**：

| collection | 内容 | 向量 |
|---|---|---|
| `knowledge_{companion_id}` | 分块 | embedding 维度（如 4096） |
| `knowledge_meta_{companion_id}` | 文档元数据 | 1 维占位向量 |

**为什么元数据要单独一个 collection**：Qdrant 要求每个 point 都携带向量，
且同一 collection 内向量维度必须一致。分块是 embedding 维度，而元数据本身
不需要向量——混在一起就只能给元数据塞一个 4096 维零向量，既浪费又语义怪异。
单独建一个 1 维 collection 更干净，也让「元数据」与「分块」的职责边界清晰：
前者回答「有哪些文档」，后者回答「文档里有什么」。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.memory.knowledge.base import (
    KnowledgeChunk,
    KnowledgeDoc,
    KnowledgeHit,
    KnowledgeStore,
)
from app.memory.naming import normalize_scope_id
from app.memory.warm.embedding import EmbeddingProvider, create_embedding_provider

#: collection 名前缀
CHUNK_PREFIX = "knowledge_"
META_PREFIX = "knowledge_meta_"
#: 元数据 collection 的占位向量维度（Qdrant 要求 point 必须带向量）
META_VECTOR_SIZE = 1
#: scroll 分页大小
_SCROLL_LIMIT = 256
#: 按文档过滤的字段名
_FIELD_DOC_ID = "doc_id"
#: 作用域原文的字段名（collection 名带哈希后缀，反解不回来，只能存）
_FIELD_SCOPE = "scope"


def _chunk_point_id(doc_id: str, index: int) -> str:
    """分块 point id：**确定性** UUID。

    确定性（而非随机）让「同一文档同一序号」的覆盖写自然落到同一个 point 上，
    重复上传同一文档时不会留下旧分块。
    """
    return str(uuid5(NAMESPACE_URL, f"hypra-kb:{doc_id}:{index}"))


def _doc_point_id(doc_id: str) -> str:
    """文档元数据 point id。"""
    return str(uuid5(NAMESPACE_URL, f"hypra-kb-doc:{doc_id}"))


def _guess_scope_from_collection(name: str) -> str:
    """从 collection 名（已含 `knowledge_meta_` 前缀）反推作用域键的近似值。

    无非法字符时 `normalize_scope_id` 是恒等映射，名字里那截就是作用域原文
    （`knowledge_meta_therapist` → `therapist`），此时反推是**精确**的。
    含非法字符时名字形如 `..._therapist_elder_sister_999284`，末尾 6 位是防碰撞
    哈希，反推只能得到近似值——所以只用于老数据兜底与粗筛，
    精确比对一律走 payload 里的 `scope` 字段。
    """
    return name[len(META_PREFIX) :] if name.startswith(META_PREFIX) else name


def _filter_scope_keys(
    keys: list[str], *, prefix: str, contains: str, exact: str, limit: int
) -> list[str]:
    """作用域键的递进过滤（本地做；两个实现同一口径，见 `base.list_scopes`）。

    `keys` 是**短字符串**（作用域键），不含任何知识正文，因此本地过滤没有成本问题；
    而 `exact` 命中时绕过 `limit`（纯存在性查询，不该被批量上限截断）。
    """
    if exact:
        return [key for key in keys if key == exact]
    out = [
        key
        for key in keys
        if (not prefix or key.startswith(prefix)) and (not contains or contains in key)
    ]
    return out[:limit]


class QdrantKnowledgeStore(KnowledgeStore):
    """基于 Qdrant 的个人记忆实现。"""

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

    @property
    def provider(self) -> EmbeddingProvider:
        return self._provider

    # ---------- collection 管理 ----------

    def _chunk_collection(self, companion_id: str) -> str:
        name = f"{CHUNK_PREFIX}{normalize_scope_id(companion_id)}"
        if not self._client.collection_exists(name):
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=self._provider.dimension, distance=Distance.COSINE
                ),
            )
        return name

    def _meta_collection(self, companion_id: str) -> str:
        name = f"{META_PREFIX}{normalize_scope_id(companion_id)}"
        if not self._client.collection_exists(name):
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(
                    size=META_VECTOR_SIZE, distance=Distance.COSINE
                ),
            )
        return name

    def _existing_chunk_collection(self, companion_id: str) -> str | None:
        """已存在的分块 collection（不存在则 None，避免只读操作意外建库）。"""
        name = f"{CHUNK_PREFIX}{normalize_scope_id(companion_id)}"
        return name if self._client.collection_exists(name) else None

    def _existing_meta_collection(self, companion_id: str) -> str | None:
        name = f"{META_PREFIX}{normalize_scope_id(companion_id)}"
        return name if self._client.collection_exists(name) else None

    @staticmethod
    def _doc_filter(doc_id: str) -> Filter:
        return Filter(
            must=[FieldCondition(key=_FIELD_DOC_ID, match=MatchValue(value=doc_id))]
        )

    # ---------- payload 转换 ----------

    @staticmethod
    def _payload_to_doc(payload: dict | None, companion_id: str) -> KnowledgeDoc:
        payload = payload or {}
        created_raw = payload.get("created_at")
        return KnowledgeDoc(
            doc_id=str(payload.get("doc_id") or ""),
            companion_id=companion_id,
            title=str(payload.get("title") or ""),
            source_type=str(payload.get("source_type") or "text"),
            chunk_count=int(payload.get("chunk_count") or 0),
            created_at=(
                datetime.fromisoformat(created_raw) if created_raw else datetime.now()
            ),
            doc_md5=str(payload.get("doc_md5") or ""),
            content_md5=str(payload.get("content_md5") or ""),
            minhash=tuple(int(value) for value in (payload.get("minhash") or [])),
        )

    @staticmethod
    def _payload_to_chunk(
        point_id: object, payload: dict | None, companion_id: str
    ) -> KnowledgeChunk:
        payload = payload or {}
        created_raw = payload.get("created_at")
        return KnowledgeChunk(
            chunk_id=str(point_id),
            doc_id=str(payload.get(_FIELD_DOC_ID) or ""),
            companion_id=companion_id,
            text=str(payload.get("text") or ""),
            index=int(payload.get("chunk_index") or 0),
            created_at=(
                datetime.fromisoformat(created_raw) if created_raw else datetime.now()
            ),
        )

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
        created = datetime.now()

        chunk_collection = self._chunk_collection(companion_id)
        meta_collection = self._meta_collection(companion_id)

        # 覆盖语义：先清掉同名文档的旧分块，避免孤儿
        self.delete_document(companion_id, resolved_id)

        if chunks:
            self._client.upsert(
                collection_name=chunk_collection,
                points=[
                    PointStruct(
                        id=_chunk_point_id(resolved_id, index),
                        vector=self._provider.embed(text),
                        payload={
                            "text": text,
                            _FIELD_DOC_ID: resolved_id,
                            "chunk_index": index,
                            "created_at": created.isoformat(),
                        },
                    )
                    for index, text in enumerate(chunks)
                ],
            )

        self._client.upsert(
            collection_name=meta_collection,
            points=[
                PointStruct(
                    id=_doc_point_id(resolved_id),
                    vector=[0.0] * META_VECTOR_SIZE,
                    payload={
                        _FIELD_DOC_ID: resolved_id,
                        # 作用域原文落进 payload：collection 名经
                        # `normalize_scope_id` 加了防碰撞哈希后缀，**反解不回来**，
                        # 而清理逻辑要按 scope 原文比对（见 base.list_scopes）。
                        _FIELD_SCOPE: companion_id,
                        "title": title,
                        "source_type": source_type,
                        "chunk_count": len(chunks),
                        "created_at": created.isoformat(),
                        "doc_md5": doc_md5,
                        "content_md5": content_md5,
                        "minhash": list(minhash),
                    },
                )
            ],
        )

        return KnowledgeDoc(
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

    def search(
        self, companion_id: str, query: str, *, top_k: int = 5
    ) -> list[KnowledgeHit]:
        if top_k <= 0:
            return []
        name = self._existing_chunk_collection(companion_id)
        if name is None:
            return []

        response = self._client.query_points(
            collection_name=name,
            query=self._provider.embed(query),
            limit=top_k,
            with_payload=True,
        )
        return [
            KnowledgeHit(
                chunk=self._payload_to_chunk(point.id, point.payload, companion_id),
                score=float(point.score),
                raw_similarity=float(point.score),
            )
            for point in response.points
        ]

    def list_documents(self, companion_id: str) -> list[KnowledgeDoc]:
        name = self._existing_meta_collection(companion_id)
        if name is None:
            return []

        docs: list[KnowledgeDoc] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=name,
                limit=_SCROLL_LIMIT,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            docs.extend(
                self._payload_to_doc(point.payload, companion_id) for point in points
            )
            if offset is None:
                break

        docs.sort(key=lambda doc: doc.created_at, reverse=True)
        return docs

    def get_document(self, companion_id: str, doc_id: str) -> KnowledgeDoc | None:
        name = self._existing_meta_collection(companion_id)
        if name is None:
            return None
        points = self._client.retrieve(
            collection_name=name,
            ids=[_doc_point_id(doc_id)],
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            return None
        return self._payload_to_doc(points[0].payload, companion_id)

    def delete_document(self, companion_id: str, doc_id: str) -> int:
        removed = 0
        chunk_collection = self._existing_chunk_collection(companion_id)
        if chunk_collection is not None:
            # 先数再删：Qdrant 的 delete 不返回命中条数
            removed = self._client.count(
                collection_name=chunk_collection,
                count_filter=self._doc_filter(doc_id),
                exact=True,
            ).count
            if removed:
                self._client.delete(
                    collection_name=chunk_collection,
                    points_selector=self._doc_filter(doc_id),
                )

        meta_collection = self._existing_meta_collection(companion_id)
        if meta_collection is not None:
            self._client.delete(
                collection_name=meta_collection,
                points_selector=[_doc_point_id(doc_id)],
            )
        return removed

    def clear_scope(self, companion_id: str) -> int:
        """删除该陪伴对象的两个 collection（分块 + 文档元数据），返回被删除的分块数。

        元数据 collection 一起删：只删分块会留下一堆「文档还在、内容没了」的
        残影，列表上仍然看得见。两个 collection 都用 `_existing_*`，
        不存在就跳过（清空一个空库不该顺手建库）。
        """
        removed = 0
        chunk_collection = self._existing_chunk_collection(companion_id)
        if chunk_collection is not None:
            removed = self._client.count(collection_name=chunk_collection).count
            self._client.delete_collection(collection_name=chunk_collection)

        meta_collection = self._existing_meta_collection(companion_id)
        if meta_collection is not None:
            self._client.delete_collection(collection_name=meta_collection)
        return int(removed)

    def list_chunks(self, companion_id: str) -> list[KnowledgeChunk]:
        name = self._existing_chunk_collection(companion_id)
        if name is None:
            return []

        chunks: list[KnowledgeChunk] = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=name,
                limit=_SCROLL_LIMIT,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            chunks.extend(
                self._payload_to_chunk(point.id, point.payload, companion_id)
                for point in points
            )
            if offset is None:
                break

        chunks.sort(key=lambda chunk: (chunk.doc_id, chunk.index))
        return chunks

    def count_chunks(self, companion_id: str) -> int:
        name = self._existing_chunk_collection(companion_id)
        if name is None:
            return 0
        return self._client.count(collection_name=name).count

    # ---------- 作用域枚举（供清理逻辑） ----------

    def list_scopes(
        self,
        *,
        prefix: str = "",
        contains: str = "",
        exact: str = "",
        limit: int = 500,
    ) -> list[str]:
        """列出真的有数据的作用域键（从**元数据 collection** 的 payload 读原文）。

        为什么读 payload 而不是解析 collection 名：`knowledge_meta_{name}` 里的
        `name` 经 `normalize_scope_id` 处理过——含非法字符时会追加 6 位哈希后缀，
        而哈希**无法反解**（`tavern:book:bk-abc` → `tavern_book_bk-abc_9f2c11`，
        末尾那截是 MD5 的摘要，不是原文）。所以作用域原文在写入时被落进 payload
        的 `scope` 字段（见 `add_document`），枚举时按它取回。

        用 meta collection 而非分块 collection：前者一篇文档一个 point（数量小得多），
        且「有哪些作用域」本来就是元数据层面的问题。

        过滤在**本地**做：一次 scroll 拿回的全是短字符串（scope 键），
        而按 collection 名粗筛会把 `a-b` / `a_b` 这类同名变体混为一谈
        （两者规范化后带不同哈希后缀，从名字反推的都不可靠）。
        """
        return _filter_scope_keys(
            self._all_scope_keys(),
            prefix=prefix,
            contains=contains,
            exact=exact,
            limit=limit,
        )

    def _all_scope_keys(self) -> list[str]:
        """扫全部元数据 collection，取回 payload 里的作用域原文（去重排序）。

        老数据（`scope` 字段引入前写入的）没有这个字段，此时按 collection 名
        反推一个**近似键**兜底——含哈希后缀的名字反推不精确，但至少能让
        「前缀 / 子串」这种宽匹配扫到老遗留物，不至于完全漏掉。
        """
        found: set[str] = set()
        for collection in self._client.get_collections().collections:
            if not collection.name.startswith(META_PREFIX):
                continue
            offset = None
            while True:
                points, offset = self._client.scroll(
                    collection_name=collection.name,
                    limit=_SCROLL_LIMIT,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in points:
                    scope = str((point.payload or {}).get(_FIELD_SCOPE) or "")
                    found.add(scope or _guess_scope_from_collection(collection.name))
                if offset is None:
                    break
        return sorted(key for key in found if key)

    def iter_scopes(
        self,
        *,
        prefix: str = "",
        contains: str = "",
        exact: str = "",
        predicate: Callable[[str], bool] | None = None,
        limit: int = 500,
    ) -> list[str]:
        """带谓词的作用域枚举（同样基于 payload 里的 scope 原文）。

        `predicate` 让调用方表达「去掉 `tavern:book:` 前缀再比哈希」这类条件
        （见 `datasource_sync.discover_stored_tavern_scopes` 用它挑旧格式键），
        而无需把所有作用域原文先拉回内存判断。返回的仍是作用域**键字符串**。
        """
        keys = self.list_scopes(
            prefix=prefix, contains=contains, exact=exact, limit=limit
        )
        if predicate is None:
            return keys
        return [key for key in keys if predicate(key)]
