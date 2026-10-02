"""个人记忆（知识库）的数据模型与存储接口。

隔离：按 `companion_id` 划分命名空间——与情景记忆同一维度，但**独立存储**
（知识是用户主动上传的，不是对话产生的）。

与情景记忆的三处**刻意差异**：

| | 情景记忆（warm） | 个人记忆（knowledge） |
|---|---|---|
| 时间衰减 | 有（30 天半衰期） | **无**——知识不老化 |
| 情绪加权 | 有 | **无**——知识是客观的 |
| 淘汰方式 | `last_recalled_at` 超 TTL 自动删除 | **用户主动删除** |

检索这里只提供**稠密通道**（相似度）；BM25 稀疏通道由上层用
`list_chunks()` 构建索引后再 RRF 融合——与情景记忆共用
`app/rag/retrieval` 的底座，不重复实现一套检索。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class KnowledgeDoc:
    """一篇已入库文档的元数据（不含正文）。"""

    doc_id: str
    companion_id: str
    title: str
    #: 来源格式：text | md | pdf | docx
    source_type: str
    chunk_count: int
    created_at: datetime
    #: 原始字节 MD5（一级去重）
    doc_md5: str = ""
    #: 清洗后文本 MD5（二级去重）
    content_md5: str = ""
    #: MinHash 签名（三级近似去重）
    minhash: tuple[int, ...] = ()


@dataclass
class KnowledgeChunk:
    """文档的一个分块。"""

    chunk_id: str
    doc_id: str
    companion_id: str
    text: str
    #: 在所属文档中的序号（0 起）
    index: int
    created_at: datetime
    vector: list[float] = field(default_factory=list, repr=False)


@dataclass
class KnowledgeHit:
    """一次检索命中的分块。"""

    chunk: KnowledgeChunk
    score: float
    raw_similarity: float = 0.0


class KnowledgeStore(ABC):
    """个人记忆存储抽象（双实现：内存 / Qdrant）。"""

    @abstractmethod
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
        """保存一篇文档（连同全部分块），返回其元数据。

        同一个 `doc_id` 再次调用表示**覆盖**：旧分块必须先被清除，否则会残留
        「孤儿分块」——检索能命中它，但它已不属于任何文档，删除文档时也带不走。
        """

    @abstractmethod
    def search(
        self, companion_id: str, query: str, *, top_k: int = 5
    ) -> list[KnowledgeHit]:
        """稠密检索（混合检索的稠密通道），按相似度降序返回前 top_k。"""

    @abstractmethod
    def list_documents(self, companion_id: str) -> list[KnowledgeDoc]:
        """列出全部文档（按创建时间降序，供去重比对与展示）。"""

    @abstractmethod
    def get_document(self, companion_id: str, doc_id: str) -> KnowledgeDoc | None:
        """按 id 取单篇文档元数据（无则 None）。"""

    @abstractmethod
    def delete_document(self, companion_id: str, doc_id: str) -> int:
        """删除整篇文档及其全部分块，返回被删除的分块数。"""

    @abstractmethod
    def list_chunks(self, companion_id: str) -> list[KnowledgeChunk]:
        """列出全部分块（供上层构建 BM25 稀疏索引）。

        这是全量读取，**不要**每轮对话都调用——上层应缓存索引。
        """

    @abstractmethod
    def count_chunks(self, companion_id: str) -> int:
        """该陪伴对象当前的分块总数。"""

    @abstractmethod
    def clear_scope(self, companion_id: str) -> int:
        """删除该陪伴对象的**全部**文档与分块，返回被删除的分块数。

        用在这里的场景是用户要「彻底忘掉这个角色」（知识是用户自己上传的，
        删掉不回影响任何人）；没有数据时返回 0，不报错。不可恢复。
        """
