"""个人记忆存储测试。

同一套用例**参数化跑两个后端**：接口一致是硬要求——温层曾经因为两个后端的
候选集策略不同，导致「开发环境好、评审环境不一样」，这里从一开始就用同一
份断言卡住两边。
"""

import pytest

from app.memory.knowledge.base import KnowledgeStore
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.knowledge.qdrant_store import QdrantKnowledgeStore

COMPANION = "therapist"

CHUNKS_A = [
    "苏澄的咨询室在老城区一栋安静的二层小楼里，浅米色墙面。",
    "茶几上常年放着一壶温热的茉莉花茶与一盒纸巾。",
]
CHUNKS_B = ["量子纠缠描述了粒子之间非局域的关联。"]


@pytest.fixture(params=["memory", "qdrant"])
def store(request) -> KnowledgeStore:
    if request.param == "memory":
        return InMemoryKnowledgeStore()
    return QdrantKnowledgeStore(local_path=":memory:")


# ---------- 文档管理 ----------


def test_add_and_list_documents(store: KnowledgeStore) -> None:
    doc = store.add_document(
        COMPANION, title="咨询室", chunks=CHUNKS_A, source_type="md"
    )

    assert doc.doc_id
    assert doc.chunk_count == 2

    docs = store.list_documents(COMPANION)
    assert [item.doc_id for item in docs] == [doc.doc_id]
    assert docs[0].title == "咨询室"
    assert docs[0].source_type == "md"
    assert docs[0].chunk_count == 2


def test_metadata_roundtrip(store: KnowledgeStore) -> None:
    """三级去重所需的三级指纹必须原样存取，否则去重会失效。"""
    doc = store.add_document(
        COMPANION,
        title="x",
        chunks=CHUNKS_A,
        doc_md5="file-digest",
        content_md5="content-digest",
        minhash=(1, 2, 3, 4, 5),
    )

    got = store.get_document(COMPANION, doc.doc_id)
    assert got is not None
    assert got.doc_md5 == "file-digest"
    assert got.content_md5 == "content-digest"
    assert got.minhash == (1, 2, 3, 4, 5)


def test_get_missing_document_returns_none(store: KnowledgeStore) -> None:
    assert store.get_document(COMPANION, "不存在") is None


def test_readd_same_doc_id_overwrites_without_orphans(store: KnowledgeStore) -> None:
    """同 doc_id 覆盖写不得留下孤儿分块（能被检索到却不属于任何文档）。"""
    first = store.add_document(
        COMPANION, title="v1", chunks=["第一版内容一", "第一版内容二"]
    )
    second = store.add_document(
        COMPANION, title="v2", chunks=["第二版内容"], doc_id=first.doc_id
    )

    assert second.doc_id == first.doc_id
    assert store.count_chunks(COMPANION) == 1
    assert [chunk.text for chunk in store.list_chunks(COMPANION)] == ["第二版内容"]
    assert store.list_documents(COMPANION)[0].title == "v2"


# ---------- 检索 ----------


def test_search_ranks_relevant_chunk_first(store: KnowledgeStore) -> None:
    store.add_document(COMPANION, title="咨询室", chunks=CHUNKS_A)
    store.add_document(COMPANION, title="量子", chunks=CHUNKS_B)

    hits = store.search(COMPANION, "茉莉花茶", top_k=2)

    assert hits
    assert "茉莉花茶" in hits[0].chunk.text


def test_search_empty_store(store: KnowledgeStore) -> None:
    assert store.search(COMPANION, "任何问题") == []


def test_top_k_limits(store: KnowledgeStore) -> None:
    store.add_document(COMPANION, title="a", chunks=CHUNKS_A)
    assert len(store.search(COMPANION, "苏澄", top_k=1)) == 1


def test_list_chunks_sorted(store: KnowledgeStore) -> None:
    doc = store.add_document(COMPANION, title="a", chunks=["块0", "块1", "块2"])

    chunks = store.list_chunks(COMPANION)
    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    assert all(chunk.doc_id == doc.doc_id for chunk in chunks)


# ---------- 删除与隔离 ----------


def test_delete_document_removes_chunks_and_meta(store: KnowledgeStore) -> None:
    doc = store.add_document(COMPANION, title="a", chunks=CHUNKS_A)
    assert store.count_chunks(COMPANION) == 2

    removed = store.delete_document(COMPANION, doc.doc_id)

    assert removed == 2
    assert store.count_chunks(COMPANION) == 0
    assert store.list_documents(COMPANION) == []
    assert store.get_document(COMPANION, doc.doc_id) is None
    assert store.search(COMPANION, "茉莉花茶") == []


def test_isolated_by_companion(store: KnowledgeStore) -> None:
    store.add_document("a", title="A 的资料", chunks=["A 的内容"])
    store.add_document("b", title="B 的资料", chunks=["B 的内容"])

    assert store.count_chunks("a") == 1
    assert store.count_chunks("b") == 1

    store.delete_document("a", store.list_documents("a")[0].doc_id)

    assert store.count_chunks("a") == 0
    assert store.count_chunks("b") == 1          # B 不受影响
    assert [doc.title for doc in store.list_documents("b")] == ["B 的资料"]


def test_read_only_ops_on_unused_namespace(store: KnowledgeStore) -> None:
    """只读操作不应意外建库，且一律安全返回空值。"""
    assert store.list_documents("never-used") == []
    assert store.list_chunks("never-used") == []
    assert store.count_chunks("never-used") == 0
    assert store.search("never-used", "x") == []
    assert store.delete_document("never-used", "any") == 0


# ---------- 作用域枚举（清理逻辑的依赖） ----------
#
# 两个后端都要能**按作用域原文**枚举出有数据的库。清理逻辑据此找到
# 「书已从酒馆删掉、文档还留在库里」的残留（见 `datasource_sync.discover_tavern_scopes`）。
# Qdrant 侧的难点：collection 名经 `normalize_scope_id` 加了防碰撞哈希后缀，
# 反解不回来——所以 scope 原文必须落进 payload。


def test_list_scopes_returns_scope_keys(store: KnowledgeStore) -> None:
    store.add_document("a", title="A", chunks=["内容"])
    store.add_document("tavern:book:bk-abc", title="书", chunks=["内容"])

    keys = store.list_scopes()

    assert set(keys) == {"a", "tavern:book:bk-abc"}


def test_list_scopes_prefix_and_contains(store: KnowledgeStore) -> None:
    store.add_document("tavern:book:bk-abc", title="书", chunks=["内容"])
    store.add_document("tavern:legacy:tbp-1", title="旧", chunks=["内容"])
    store.add_document("user-x", title="私有", chunks=["内容"])

    assert store.list_scopes(prefix="tavern:book:") == ["tavern:book:bk-abc"]
    assert store.list_scopes(prefix="tavern:") == [
        "tavern:book:bk-abc",
        "tavern:legacy:tbp-1",
    ]
    assert store.list_scopes(contains="tbp-1") == ["tavern:legacy:tbp-1"]
    assert store.list_scopes(exact="user-x") == ["user-x"]
    assert store.list_scopes(exact="不存在") == []


def test_list_scopes_survives_unsafe_characters(store: KnowledgeStore) -> None:
    """★ 作用域键含冒号/连字符（Qdrant collection 名必须转义）时也能原样枚举。

    这是 Qdrant 侧的核心风险：`normalize_scope_id("tavern:book:bk-abc")` 会变成
    `tavern_book_bk-abc_<哈希>`，末尾那截无法反解——只有把 scope 原文存进
    payload 才能还原，否则清理逻辑永远找不到这些库。
    """
    scope = "tavern:book:bk-deadbeef1234"
    store.add_document(scope, title="书", chunks=["内容"])

    assert store.list_scopes(exact=scope) == [scope]
    assert scope in store.list_scopes(prefix="tavern:book:")


def test_iter_scopes_applies_predicate(store: KnowledgeStore) -> None:
    store.add_document("tavern:book:bk-1", title="新", chunks=["内容"])
    store.add_document("tavern:legacy:tbp-1", title="旧", chunks=["内容"])

    legacy = store.iter_scopes(
        prefix="tavern:",
        predicate=lambda key: not key.startswith("tavern:book:"),
    )

    assert legacy == ["tavern:legacy:tbp-1"]


def test_list_scopes_empty_store(store: KnowledgeStore) -> None:
    """没有数据的库不该报错，也不该顺手建库。"""
    assert store.list_scopes() == []
    assert store.iter_scopes(predicate=lambda key: True) == []


def test_deleting_last_document_removes_scope(store: KnowledgeStore) -> None:
    """★ 文档删空后作用域就该消失——否则清理逻辑会一直看到幽灵键。"""
    doc = store.add_document("a", title="A", chunks=["内容"])
    assert store.list_scopes() == ["a"]

    store.delete_document("a", doc.doc_id)

    assert store.list_scopes() == []
