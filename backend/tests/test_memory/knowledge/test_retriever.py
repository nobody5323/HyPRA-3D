"""知识库混合检索测试。"""

import pytest

from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.knowledge.retriever import KnowledgeRetriever

COMPANION = "therapist"
CHUNKS_ROOM = ["苏澄的咨询室在老城区一栋安静的二层小楼里，浅米色墙面。"]
CHUNKS_ORION = ["小林公司的项目代号是 ORION-7，下周要交付。"]


@pytest.fixture()
def retriever() -> KnowledgeRetriever:
    store = InMemoryKnowledgeStore()
    store.add_document(COMPANION, title="咨询室", chunks=CHUNKS_ROOM)
    store.add_document(COMPANION, title="项目", chunks=CHUNKS_ORION)
    return KnowledgeRetriever(store, top_k=2, candidate_n=10)


def test_retrieve_returns_relevant_chunk(retriever: KnowledgeRetriever) -> None:
    hits = retriever.retrieve(COMPANION, "咨询室在哪里")

    assert hits
    assert "咨询室" in hits[0].chunk.text


def test_lexical_channel_recalls_exact_term(retriever: KnowledgeRetriever) -> None:
    """BM25 通道能命中专有编号——这正是向量通道容易漂移的场景。"""
    hits = retriever.retrieve(COMPANION, "ORION-7 的进度")

    assert hits
    assert "ORION-7" in hits[0].chunk.text


def test_blank_query_and_empty_store() -> None:
    empty = KnowledgeRetriever(InMemoryKnowledgeStore())

    assert empty.retrieve(COMPANION, "任何问题") == []
    assert empty.retrieve(COMPANION, "   ") == []


def test_top_k_limits_results(retriever: KnowledgeRetriever) -> None:
    assert len(retriever.retrieve(COMPANION, "苏澄", top_k=1)) <= 1


def test_index_rebuilds_after_upload() -> None:
    """上传新文档后无需显式通知即可检索到（chunk 数变化即重建）。"""
    store = InMemoryKnowledgeStore()
    retriever = KnowledgeRetriever(store)
    assert retriever.retrieve(COMPANION, "新上传的内容") == []

    store.add_document(COMPANION, title="新文档", chunks=["新上传的内容就在这里。"])

    assert retriever.retrieve(COMPANION, "新上传的内容")


def test_index_rebuilds_after_delete() -> None:
    """删除文档后，BM25 索引不应继续召回已删除的内容。"""
    store = InMemoryKnowledgeStore()
    doc = store.add_document(COMPANION, title="项目", chunks=CHUNKS_ORION)
    retriever = KnowledgeRetriever(store)
    assert retriever.retrieve(COMPANION, "ORION-7")   # 先建索引并命中

    store.delete_document(COMPANION, doc.doc_id)

    assert retriever.retrieve(COMPANION, "ORION-7") == []


def test_invalidate_forces_rebuild(retriever: KnowledgeRetriever) -> None:
    retriever.invalidate(COMPANION)
    assert retriever.retrieve(COMPANION, "咨询室")

    retriever.invalidate()
    assert retriever.retrieve(COMPANION, "咨询室")
