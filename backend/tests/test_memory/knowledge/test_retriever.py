"""知识库混合检索测试。"""

import pytest

from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.knowledge.retriever import KnowledgeRetriever
from app.memory.knowledge.scopes import book_scope
from app.worldbook.models import SCOPE_ALL

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


# =============================================================
# 共享作用域（全局世界书只存一份，所有陪伴对象都能召回）
# =============================================================


def test_global_scope_is_also_searched() -> None:
    """★ 检索同时查「本陪伴对象 + 共享作用域」：全局设定所有角色都能召回。

    全球世界书（`scope="*"`）只存一份，不按陪伴对象复制 N 份；
    要是检索只看本作用域，那些共享设定就永远召不回。
    """
    store = InMemoryKnowledgeStore()
    store.add_document(SCOPE_ALL, title="世界观", chunks=["这座城市靠海，冬天多雨。"])
    retriever = KnowledgeRetriever(store, top_k=2, candidate_n=10)

    hits = retriever.retrieve("任意陪伴对象", "这座城市在哪里")

    assert hits
    assert "靠海" in hits[0].chunk.text


def test_character_scope_is_not_leaked_to_others() -> None:
    """★ 反向：某个角色专属的设定不能被别的陪伴对象召回（§8.2 的隔离）。"""
    store = InMemoryKnowledgeStore()
    store.add_document("tbp-甲", title="专属", chunks=["甲的秘密只有甲知道。"])
    store.add_document(SCOPE_ALL, title="共享", chunks=["公开的设定。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    texts = [hit.chunk.text for hit in retriever.retrieve("tbp-乙", "甲的秘密是什么")]

    # 共享作用域里可能召回无关内容（那是检索质量的事），
    # 但**别人的专属内容绝不能被召回**
    assert all("甲的秘密" not in text for text in texts)


def test_both_scopes_merge_into_one_ranking() -> None:
    """两个作用域的命中合入**同一个** RRF 排序，而不是拼成两段。"""
    store = InMemoryKnowledgeStore()
    store.add_document(SCOPE_ALL, title="共享", chunks=["苏澄的咨询室在一楼。"])
    store.add_document("tbp-甲", title="专属", chunks=["苏澄的咨询室在二楼。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    hits = retriever.retrieve("tbp-甲", "苏澄的咨询室在几楼")

    assert len(hits) == 2
    # RRF 分数单调递减——两个作用域的内容是同一张排序表
    assert hits[0].score >= hits[1].score


def test_querying_the_shared_scope_does_not_duplicate_it() -> None:
    """陪伴对象就是共享作用域时不重复查两遍（分数会虚高）。"""
    store = InMemoryKnowledgeStore()
    store.add_document(SCOPE_ALL, title="共享", chunks=["只有一条。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    hits = retriever.retrieve(SCOPE_ALL, "只有一条")

    assert len(hits) == 1


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


# =============================================================
# 酒馆世界书:只在酒馆模式 + 只查已挂载的书(AGENTS.md §8.1 / §8.2)
# =============================================================

#: 假想的「一本已挂载的世界书」的来源标识与它对应的作用域
BOOK_A = "world/世界甲"
SCOPE_A = book_scope(BOOK_A)


def test_mounted_book_is_not_searched_in_companion_mode() -> None:
    """★ 桌宠模式查不到酒馆世界书——内置人设不该「知道」别的作品的设定。

    这就是「酒馆世界书注入向量库后召回不出来」的真实期望行为：
    在桌宠模式里**就该**召不回来。混进共享作用域才是 bug。
    """
    store = InMemoryKnowledgeStore()
    store.add_document(SCOPE_A, title="酒馆世界书", chunks=["某作品的角色设定。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    # 桌宠模式即使挂了全书也不查
    assert retriever.retrieve(COMPANION, "某作品的角色设定", mounted_books=[BOOK_A]) == []


def test_default_mounts_nothing_so_no_book_is_searched() -> None:
    """★ 默认全不挂：酒馆模式下，没挂的书一本都查不到。"""
    store = InMemoryKnowledgeStore()
    store.add_document(SCOPE_A, title="甲书", chunks=["甲书设定：这座城市靠海。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    assert retriever.retrieve(COMPANION, "这座城市靠海", include_tavern=True) == []


def test_mounted_book_is_searched_in_tavern_mode() -> None:
    """★ 酒馆模式 + 已挂载 → 能召回这本书的内容。"""
    store = InMemoryKnowledgeStore()
    store.add_document(SCOPE_A, title="甲书", chunks=["甲书设定：这座城市靠海。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    hits = retriever.retrieve(
        COMPANION, "这座城市靠海", include_tavern=True, mounted_books=[BOOK_A]
    )

    assert hits and "靠海" in hits[0].chunk.text


def test_unmounted_book_stays_isolated() -> None:
    """★ 没挂的书不串味：挂着甲书时查不到乙书的内容。"""
    store = InMemoryKnowledgeStore()
    book_b = "world/世界乙"
    store.add_document(book_scope(book_b), title="乙书", chunks=["乙书专属：她住在城南。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    assert retriever.retrieve(
        COMPANION, "她住在城南", include_tavern=True, mounted_books=[BOOK_A]
    ) == []


def test_multiple_mounted_books_are_all_searched() -> None:
    """★ 挂多本时每本都能召回（挂哪几本查哪几本）。"""
    store = InMemoryKnowledgeStore()
    book_b = "char/角色乙"
    store.add_document(SCOPE_A, title="甲书", chunks=["甲书设定：这座城市靠海。"])
    store.add_document(book_scope(book_b), title="乙书", chunks=["乙书设定：她住在城南。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    hit_a = retriever.retrieve(
        COMPANION, "这座城市靠海", include_tavern=True, mounted_books=[BOOK_A, book_b]
    )
    hit_b = retriever.retrieve(
        COMPANION, "她住在城南", include_tavern=True, mounted_books=[BOOK_A, book_b]
    )

    assert hit_a and "靠海" in hit_a[0].chunk.text
    assert hit_b and "城南" in hit_b[0].chunk.text


def test_tavern_mode_still_searches_user_uploaded_knowledge() -> None:
    """酒馆模式**不关闭**基础作用域：用户自己上传的语料两种模式都能用。"""
    store = InMemoryKnowledgeStore()
    store.add_document(COMPANION, title="我上传的", chunks=["我的私人语料在这里。"])
    retriever = KnowledgeRetriever(store, top_k=5, candidate_n=10)

    hits = retriever.retrieve(COMPANION, "我的私人语料", include_tavern=True)

    assert hits and "私人语料" in hits[0].chunk.text


# =============================================================
# 通道策略分档：酒馆世界书「稠密优先」，其余来源保持原混合
# =============================================================


def _spy_sparse_scopes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """记录哪些作用域真的走了稀疏通道（会构建 BM25 索引）。

    直接断言 BM25 分数不现实（测试用的是确定性 embedding），
    所以改成断言**通道是否被启用**——这正是本次改动的边界。
    """
    seen: list[str] = []
    original = KnowledgeRetriever._lexical_view

    def spy(self: KnowledgeRetriever, companion_id: str):
        seen.append(companion_id)
        return original(self, companion_id)

    monkeypatch.setattr(KnowledgeRetriever, "_lexical_view", spy)
    return seen


def test_tavern_scope_skips_sparse_channel_when_dense_is_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 酒馆来源「稠密优先」：稠密候选够用时**不再跑 BM25**。

    世界书里「衣服 / 设定」这类泛词遍地，BM25 的字面命中会在等权 RRF 里
    压过语义更相关的条目（实测把「凌颜玉的衣柜」挤出 top3）。
    """
    store = InMemoryKnowledgeStore()
    store.add_document(SCOPE_A, title="甲", chunks=["甲的内容。", "甲的另一段。"])
    store.add_document(SCOPE_A, title="乙", chunks=["乙的内容。"])
    retriever = KnowledgeRetriever(store, top_k=2, candidate_n=10)

    seen = _spy_sparse_scopes(monkeypatch)

    hits = retriever.retrieve(
        COMPANION, "内容", include_tavern=True, mounted_books=[BOOK_A]
    )

    assert hits
    assert SCOPE_A not in seen


def test_tavern_scope_falls_back_to_sparse_when_dense_is_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 稠密候选不足时，酒馆来源仍用 BM25 兜底——保住专有名词/编号的召回。"""
    store = InMemoryKnowledgeStore()
    store.add_document(SCOPE_A, title="编号", chunks=["ORION-7 的进度。"])
    retriever = KnowledgeRetriever(store, top_k=3, candidate_n=10)  # 只有 1 条 < 3

    seen = _spy_sparse_scopes(monkeypatch)

    hits = retriever.retrieve(
        COMPANION, "ORION-7", include_tavern=True, mounted_books=[BOOK_A]
    )

    assert hits
    assert SCOPE_A in seen


def test_user_uploaded_scope_keeps_the_full_hybrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 用户上传语料**不受本次改动影响**：仍然走 BM25 + 向量的完整混合。

    这是刻意的边界——收紧只针对酒馆世界书；私人语料里的专有名词/编号
    恰恰最需要 BM25（见 `test_lexical_channel_recalls_exact_term`）。
    """
    store = InMemoryKnowledgeStore()
    store.add_document(COMPANION, title="甲", chunks=["甲的内容。", "甲的另一段。"])
    retriever = KnowledgeRetriever(store, top_k=2, candidate_n=10)

    seen = _spy_sparse_scopes(monkeypatch)

    retriever.retrieve(COMPANION, "内容")

    assert COMPANION in seen
