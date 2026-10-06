"""世界书 → 个人知识库的同步测试：分篇、幂等覆盖、清理、作用域隔离。

夹具全部**自创**（`AGENTS.md §8.6` 红线）：不使用任何真实酒馆世界书内容，
只复刻它们的**结构**（全局书、角色内嵌书、同一来源多条、来源被删…）。

**每本世界书一个独立作用域**（`tavern:book:{哈希}`）：全局书与角色内嵌书
一视同仁，都是「一本可挂载的书」。
"""

from __future__ import annotations

import pytest

from app.memory.knowledge.datasource_sync import (
    SOURCE_TYPE,
    candidate_scopes,
    discover_stored_tavern_scopes,
    discover_tavern_scopes,
    group_worldbook_entries,
    mounted_book_sources,
    sync_worldbook_knowledge,
    worldbook_knowledge_status,
)
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.knowledge.scopes import TAVERN_SCOPE, book_scope
from app.plugins.contracts import DataSourceCharacter, DataSourceEntry, DataSourceSnapshot
from app.prompts.persona.datasource_map import persona_id_for
from app.worldbook.models import SCOPE_ALL


def _entry(
    title: str,
    content: str = "设定正文",
    *,
    source_id: str,
    character: str = "",
    position: str = "after_char",
) -> DataSourceEntry:
    extra = {"character": character} if character else {}
    return DataSourceEntry(
        title=title,
        content=content,
        source_id=source_id,
        source="demo-source",
        position=position,
        extra=extra,
    )


def _card(name: str) -> DataSourceCharacter:
    return DataSourceCharacter(
        name=name, description="设定正文", source="demo-source", source_id=name
    )


def _snapshot(*entries: DataSourceEntry, cards: list[DataSourceCharacter] | None = None):
    return DataSourceSnapshot(entries=list(entries), characters=list(cards or []))


@pytest.fixture
def store() -> InMemoryKnowledgeStore:
    return InMemoryKnowledgeStore()


# =============================================================
# 分篇：每本世界书一个独立作用域
# =============================================================


def test_global_entries_group_by_source_file() -> None:
    """全局世界书按**文件**分篇，每本一个独立作用域。"""
    snapshot = _snapshot(
        _entry("甲", source_id="world/世界甲#1"),
        _entry("乙", source_id="world/世界甲#2"),
        _entry("丙", source_id="world/世界乙#1"),
    )

    groups, orphaned = group_worldbook_entries(snapshot)

    assert orphaned == 0
    assert set(groups) == {
        (book_scope("world/世界甲"), "world/世界甲"),
        (book_scope("world/世界乙"), "world/世界乙"),
    }
    assert len(groups[(book_scope("world/世界甲"), "world/世界甲")]) == 2


def test_character_entries_get_their_own_book_scope() -> None:
    """★ 角色内嵌书也进**自己那本书**的作用域，与全局书一视同仁。

    不再要求「必须能找到角色卡」——内嵌书本身就是一本可挂载的书，
    作用域由来源标识（`char/<角色名>`）算出，与 persona 无关。
    """
    card = _card("示例角色")
    snapshot = _snapshot(
        _entry("专属", source_id="char/示例角色#1", character="示例角色"),
        cards=[card],
    )

    groups, orphaned = group_worldbook_entries(snapshot)

    assert orphaned == 0
    assert set(groups) == {(book_scope("char/示例角色"), "char/示例角色")}


def test_embedded_book_without_its_card_still_syncs() -> None:
    """★ 角色卡被删了，内嵌书仍能独立入库（它不再是「角色的附属品」）。"""
    snapshot = _snapshot(_entry("孤儿", source_id="char/已删角色#1", character="已删角色"))

    groups, orphaned = group_worldbook_entries(snapshot)

    assert orphaned == 0
    assert set(groups) == {(book_scope("char/已删角色"), "char/已删角色")}


def test_empty_and_outlet_entries_are_dropped() -> None:
    snapshot = _snapshot(
        _entry("空", "", source_id="world/甲#1"),
        _entry("出口", source_id="world/甲#2", position="outlet"),
        _entry("正常", source_id="world/甲#3"),
    )

    groups, _ = group_worldbook_entries(snapshot)

    scope = book_scope("world/甲")
    assert [entry.title for entry in groups[(scope, "world/甲")]] == ["正常"]


# =============================================================
# 写入：分块、幂等覆盖、清理
# =============================================================


def test_sync_writes_one_chunk_per_entry(store: InMemoryKnowledgeStore) -> None:
    snapshot = _snapshot(
        _entry("条目甲", "正文甲", source_id="world/世界甲#1"),
        _entry("条目乙", "正文乙", source_id="world/世界甲#2"),
    )

    result = sync_worldbook_knowledge(store, snapshots=[snapshot])

    scope = book_scope("world/世界甲")
    assert (result.documents, result.chunks) == (1, 2)
    assert result.scopes == {scope: 1}
    docs = store.list_documents(scope)
    assert len(docs) == 1
    assert docs[0].source_type == SOURCE_TYPE
    assert docs[0].chunk_count == 2


def test_chunk_text_carries_the_title(store: InMemoryKnowledgeStore) -> None:
    """标题进分块正文：BM25 靠字面命中，标题往往就是提问用的词。"""
    snapshot = _snapshot(_entry("人物设计_江浸月", "她住在城南。", source_id="world/甲#1"))

    sync_worldbook_knowledge(store, snapshots=[snapshot])

    (chunk,) = store.list_chunks(book_scope("world/甲"))
    assert chunk.text == "[人物设计_江浸月]\n她住在城南。"


def test_resync_overwrites_instead_of_duplicating(store: InMemoryKnowledgeStore) -> None:
    """★ 重复同步是**覆盖**：doc_id 由来源算出，不会越同步越多。"""
    first = _snapshot(_entry("甲", "旧正文", source_id="world/甲#1"))
    sync_worldbook_knowledge(store, snapshots=[first])

    second = _snapshot(
        _entry("甲", "新正文", source_id="world/甲#1"),
        _entry("乙", "新增", source_id="world/甲#2"),
    )
    result = sync_worldbook_knowledge(store, snapshots=[second])

    scope = book_scope("world/甲")
    docs = store.list_documents(scope)
    assert len(docs) == 1                    # 还是同一篇
    assert (result.documents, result.removed) == (1, 0)
    texts = [chunk.text for chunk in store.list_chunks(scope)]
    assert any("新正文" in text for text in texts)
    assert all("旧正文" not in text for text in texts)


def test_prune_removes_documents_whose_source_is_gone(store: InMemoryKnowledgeStore) -> None:
    """★ 在酒馆里删掉的世界书不该继续被召回（否则用户会遇到「删了还在答」）。"""
    both = _snapshot(
        _entry("甲", source_id="world/甲#1"),
        _entry("乙", source_id="world/乙#1"),
    )
    sync_worldbook_knowledge(store, snapshots=[both])
    assert len(store.list_documents(book_scope("world/甲"))) == 1
    assert len(store.list_documents(book_scope("world/乙"))) == 1

    only_one = _snapshot(_entry("甲", source_id="world/甲#1"))
    result = sync_worldbook_knowledge(store, snapshots=[only_one])

    assert result.removed == 1
    assert store.list_documents(book_scope("world/乙")) == []
    docs = store.list_documents(book_scope("world/甲"))
    assert len(docs) == 1
    assert docs[0].title == "[酒馆] 世界书·甲（1 条）"


def test_prune_finds_scopes_absent_from_the_snapshot(
    store: InMemoryKnowledgeStore,
) -> None:
    """★ 快照里已消失的书，其文档也要清掉——这正是「删了还在答」的根因。

    与上一条的区别：上一条里被删的书**仍在挂载清单上**（`mounted` 给了线索），
    这一条把清单也给清空，只剩「存储里真的有数据」这最后一条线索。
    旧实现只从当前快照推导作用域，因此两个用例都会漏；新实现从存储侧枚举补齐。
    """
    sync_worldbook_knowledge(
        store,
        snapshots=[
            _snapshot(
                _entry("甲", source_id="world/甲#1"),
                _entry("乙", source_id="world/乙#1"),
            )
        ],
    )
    assert len(store.list_documents(book_scope("world/乙"))) == 1

    # 乙书从酒馆删除：快照里没有它，挂载清单里也没有它
    result = sync_worldbook_knowledge(
        store,
        snapshots=[_snapshot(_entry("甲", source_id="world/甲#1"))],
        mounted=[],
    )

    assert result.removed == 1
    assert store.list_documents(book_scope("world/乙")) == []
    assert len(store.list_documents(book_scope("world/甲"))) == 1


def test_prune_removes_documents_of_an_unmounted_deleted_book(
    store: InMemoryKnowledgeStore,
) -> None:
    """★ 书删了、清单里还挂着它 → 残留当场清掉（挂载清单是第二条线索）。

    用户的操作顺序往往就是「把书删掉」再「有空去改挂载」；中间这段时间里，
    那本书的旧文档不该还能被检索到。
    """
    sync_worldbook_knowledge(
        store, snapshots=[_snapshot(_entry("乙", source_id="world/乙#1"))]
    )

    result = sync_worldbook_knowledge(
        store,
        snapshots=[_snapshot(_entry("甲", source_id="world/甲#1"))],
        mounted=["world/乙"],          # 已删，但清单里还留着
    )

    assert result.removed == 1
    assert store.list_documents(book_scope("world/乙")) == []


def test_prune_can_be_disabled(store: InMemoryKnowledgeStore) -> None:
    both = _snapshot(
        _entry("甲", source_id="world/甲#1"),
        _entry("乙", source_id="world/乙#1"),
    )
    sync_worldbook_knowledge(store, snapshots=[both])

    sync_worldbook_knowledge(
        store, snapshots=[_snapshot(_entry("甲", source_id="world/甲#1"))], prune=False
    )

    assert len(store.list_documents(book_scope("world/乙"))) == 1


def test_prune_never_touches_user_uploaded_documents(store: InMemoryKnowledgeStore) -> None:
    """★ 只清 `source_type == tavern-worldbook`：用户自己上传的语料一篇不动。"""
    store.add_document(SCOPE_ALL, title="我上传的资料", chunks=["私人语料"])
    sync_worldbook_knowledge(
        store, snapshots=[_snapshot(_entry("甲", source_id="world/甲#1"))]
    )

    result = sync_worldbook_knowledge(store, snapshots=[_snapshot()])

    assert result.removed == 1                       # 只清掉了酒馆那篇
    titles = [doc.title for doc in store.list_documents(SCOPE_ALL)]
    assert titles == ["我上传的资料"]


# =============================================================
# 作用域隔离与状态
# =============================================================


def test_books_are_kept_separate(store: InMemoryKnowledgeStore) -> None:
    """★ 每本书各进各的作用域，互相看不到（隔离单位 = 单本世界书）。"""
    snapshot = _snapshot(
        _entry("甲的设定", "甲的正文", source_id="char/角色甲#1", character="角色甲"),
        _entry("全局", "公共正文", source_id="world/公共#1"),
    )

    result = sync_worldbook_knowledge(store, snapshots=[snapshot])

    assert result.scopes == {
        book_scope("char/角色甲"): 1,
        book_scope("world/公共"): 1,
    }
    titles_a = [doc.title for doc in store.list_documents(book_scope("char/角色甲"))]
    assert len(titles_a) == 1 and "角色内嵌设定" in titles_a[0]
    # 这本书的库里没有公共书的东西
    assert all("公共" not in title for title in titles_a)


def test_status_counts_every_scope(store: InMemoryKnowledgeStore) -> None:
    """状态要看**全部候选作用域**：只看全局会漏掉其它书。"""
    snapshot = _snapshot(
        _entry("全局", source_id="world/公共#1"),
        _entry("专属", source_id="char/角色甲#1", character="角色甲"),
    )
    sync_worldbook_knowledge(store, snapshots=[snapshot])

    status = worldbook_knowledge_status(store, snapshots=[snapshot])

    assert status == {"documents": 2, "chunks": 2, "scopes": 2}


def test_status_ignores_user_uploaded_documents(store: InMemoryKnowledgeStore) -> None:
    store.add_document(SCOPE_ALL, title="我上传的", chunks=["私人语料"])

    status = worldbook_knowledge_status(store, snapshots=[_snapshot()])

    assert status["documents"] == 0


def test_empty_snapshot_is_a_noop(store: InMemoryKnowledgeStore) -> None:
    result = sync_worldbook_knowledge(store, snapshots=[_snapshot()])

    assert result.as_dict()["documents"] == 0
    assert store.list_documents(SCOPE_ALL) == []


def test_mounted_book_sources_lists_every_book() -> None:
    """挂载清单的可选来源 = 所有书（全局 + 内嵌一视同仁），去重且稳定排序。"""
    snapshot = _snapshot(
        _entry("甲", source_id="world/世界甲#1"),
        _entry("乙", source_id="world/世界甲#2"),
        _entry("丙", source_id="char/角色甲#1", character="角色甲"),
    )

    assert mounted_book_sources([snapshot]) == ["char/角色甲", "world/世界甲"]


# =============================================================
# 作用域发现：快照算不出来的那些键（「删了还在答」的根因）
# =============================================================


def test_discover_stored_tavern_scopes_finds_orphans(
    store: InMemoryKnowledgeStore,
) -> None:
    """★ 存储里有数据、快照里已没有的酒馆作用域，也要被枚举出来。

    这是清理的**第二条线索**：只靠快照算键，删掉的书就永远留在库里。
    """
    orphan = book_scope("world/已删的书")
    store.add_document(orphan, title="x", chunks=["旧内容"], source_type=SOURCE_TYPE)
    store.add_document("user-1", title="我上传的", chunks=["私人语料"])

    found = discover_stored_tavern_scopes(store)

    assert orphan in found
    assert "user-1" not in found           # 陪伴对象私有作用域不在范围内


def test_discover_stored_tavern_scopes_finds_legacy_keys(
    store: InMemoryKnowledgeStore,
) -> None:
    """★ 旧格式（`tavern` / `tavern:{角色id}`）也要被发现，否则旧数据清不掉。"""
    store.add_document(TAVERN_SCOPE, title="旧全局书", chunks=["旧内容"],
                       source_type=SOURCE_TYPE)
    store.add_document(f"{TAVERN_SCOPE}:tbp-abc123", title="旧内嵌书",
                       chunks=["旧内容"], source_type=SOURCE_TYPE)
    store.add_document(SCOPE_ALL, title="共享", chunks=["内容"], source_type=SOURCE_TYPE)

    found = discover_stored_tavern_scopes(store)

    assert TAVERN_SCOPE in found
    assert f"{TAVERN_SCOPE}:tbp-abc123" in found
    # 新格式的键不能被误判成旧格式
    assert SCOPE_ALL not in found


def test_discover_tavern_scopes_unions_three_clues(
    store: InMemoryKnowledgeStore,
) -> None:
    """三条线索并集：当前快照 ∪ 挂载清单 ∪ 存储实况。缺一条就会漏清。"""
    store.add_document(book_scope("world/残留"), title="残留", chunks=["旧"],
                       source_type=SOURCE_TYPE)

    scopes = discover_tavern_scopes(
        store,
        [_snapshot(_entry("甲", source_id="world/甲#1"))],
        mounted=["world/清单里的"],
    )

    assert book_scope("world/甲") in scopes        # 线索 1：快照
    assert book_scope("world/清单里的") in scopes  # 线索 2：挂载清单
    assert book_scope("world/残留") in scopes      # 线索 3：存储实况


def test_status_counts_orphaned_scopes(store: InMemoryKnowledgeStore) -> None:
    """★ 状态统计要看得见「书已删、文档还在」——否则界面上它是隐形的。"""
    store.add_document(book_scope("world/已删"), title="x", chunks=["旧内容"],
                       source_type=SOURCE_TYPE)

    status = worldbook_knowledge_status(store, snapshots=[_snapshot()])

    assert status == {"documents": 1, "chunks": 1, "scopes": 1}


def test_store_without_scope_enumeration_still_syncs() -> None:
    """存储不支持枚举时退化为「快照 + 清单」两条线索，不报错也不误删。"""

    class _NoEnumStore(InMemoryKnowledgeStore):
        def list_scopes(self, **kwargs):  # type: ignore[override]
            return []

        def iter_scopes(self, **kwargs):  # type: ignore[override]
            return []

    store = _NoEnumStore()
    first = sync_worldbook_knowledge(
        store, snapshots=[_snapshot(_entry("甲", source_id="world/甲#1"))]
    )
    assert first.documents == 1

    result = sync_worldbook_knowledge(store, snapshots=[_snapshot()], mounted=[])

    assert result.removed == 0                       # 枚举不了 → 清不掉（但不报错）
    assert len(store.list_documents(book_scope("world/甲"))) == 1


# =============================================================
# 存量迁移：旧版作用域（`tavern` / `tavern:{角色id}` / `*`）
# =============================================================


def test_candidate_scopes_cover_new_and_legacy_keys() -> None:
    """★ 候选作用域要同时覆盖新格式与**全部旧格式**，否则旧数据永远清不掉。"""
    card = _card("角色甲")
    snapshot = _snapshot(
        _entry("甲", source_id="world/甲#1"),
        _entry("乙", source_id="char/角色甲#1", character="角色甲"),
        cards=[card],
    )

    scopes = candidate_scopes([snapshot])

    # 新格式
    assert book_scope("world/甲") in scopes
    assert book_scope("char/角色甲") in scopes
    # 旧格式（清理用）
    assert SCOPE_ALL in scopes
    assert TAVERN_SCOPE in scopes
    assert f"{TAVERN_SCOPE}:{persona_id_for(card)}" in scopes


def test_sync_cleans_legacy_scopes(store: InMemoryKnowledgeStore) -> None:
    """★ 旧版写在 `tavern` / `*` / `tavern:{角色id}` 下的文档，同步时被清掉。

    这是「旧数据清掉重同步」的机制保证：旧作用域已不再被写入，
    只能靠 prune 遍历候选作用域把它们删掉。
    """
    card = _card("角色甲")
    snapshot = _snapshot(
        _entry("甲", source_id="world/甲#1"),
        cards=[card],
    )

    legacy_scope = f"{TAVERN_SCOPE}:{persona_id_for(card)}"
    store.add_document(
        SCOPE_ALL, title="[酒馆] 世界书·旧（1 条）", chunks=["[甲]\n旧正文"],
        source_type=SOURCE_TYPE,
    )
    store.add_document(
        TAVERN_SCOPE, title="[酒馆] 世界书·旧（1 条）", chunks=["[甲]\n旧正文"],
        source_type=SOURCE_TYPE,
    )
    store.add_document(
        legacy_scope, title="[酒馆] 角色内嵌设定·旧（1 条）", chunks=["[甲]\n旧正文"],
        source_type=SOURCE_TYPE,
    )

    result = sync_worldbook_knowledge(store, snapshots=[snapshot])

    assert result.removed == 3                          # 三处旧文档全清
    assert store.list_documents(SCOPE_ALL) == []
    assert store.list_documents(TAVERN_SCOPE) == []
    assert store.list_documents(legacy_scope) == []
    assert len(store.list_documents(book_scope("world/甲"))) == 1   # 新的落在单本作用域
