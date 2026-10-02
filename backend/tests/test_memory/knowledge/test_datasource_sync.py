"""世界书 → 个人知识库的同步测试：分篇、幂等覆盖、清理、作用域隔离。

夹具全部**自创**（`AGENTS.md §8.6` 红线）：不使用任何真实酒馆世界书内容，
只复刻它们的**结构**（全局书、角色内嵌书、同一来源多条、来源被删…）。
"""

from __future__ import annotations

import pytest

from app.memory.knowledge.datasource_sync import (
    SOURCE_TYPE,
    candidate_scopes,
    group_worldbook_entries,
    sync_worldbook_knowledge,
    worldbook_knowledge_status,
)
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.knowledge.scopes import TAVERN_SCOPE, tavern_scope
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
# 分篇：`(作用域, 来源文件)` 各一篇
# =============================================================


def test_global_entries_group_by_source_file() -> None:
    """全局世界书按**文件**分篇，不是全揉成一篇。"""
    snapshot = _snapshot(
        _entry("甲", source_id="world/世界甲#1"),
        _entry("乙", source_id="world/世界甲#2"),
        _entry("丙", source_id="world/世界乙#1"),
    )

    groups, orphaned = group_worldbook_entries(snapshot)

    assert orphaned == 0
    assert set(groups) == {
        (TAVERN_SCOPE, "world/世界甲"),
        (TAVERN_SCOPE, "world/世界乙"),
    }
    assert len(groups[(TAVERN_SCOPE, "world/世界甲")]) == 2


def test_character_entries_go_to_that_characters_scope() -> None:
    """★ 角色内嵌书归到该角色的 `tavern:{persona_id}` 作用域。

    带 `tavern` 前缀是刻意的：酒馆来源只在酒馆聊天模式召回（见 scopes 模块文档）。
    键由 `tavern_scope()` 算出，写入侧与检索侧共用同一个函数，不会各算各的。
    """
    card = _card("示例角色")
    snapshot = _snapshot(
        _entry("专属", source_id="char/示例角色#1", character="示例角色"),
        cards=[card],
    )

    groups, orphaned = group_worldbook_entries(snapshot)

    assert orphaned == 0
    assert set(groups) == {(tavern_scope(persona_id_for(card)), "char/示例角色")}


def test_entries_without_their_card_are_counted_not_misplaced() -> None:
    """找不到角色卡的内嵌书**不写库**并计数——挂到别人作用域上比不写更糟。"""
    snapshot = _snapshot(_entry("孤儿", source_id="char/已删角色#1", character="已删角色"))

    groups, orphaned = group_worldbook_entries(snapshot)

    assert groups == {}
    assert orphaned == 1


def test_empty_and_outlet_entries_are_dropped() -> None:
    snapshot = _snapshot(
        _entry("空", "", source_id="world/甲#1"),
        _entry("出口", source_id="world/甲#2", position="outlet"),
        _entry("正常", source_id="world/甲#3"),
    )

    groups, _ = group_worldbook_entries(snapshot)

    assert [entry.title for entry in groups[(TAVERN_SCOPE, "world/甲")]] == ["正常"]


# =============================================================
# 写入：分块、幂等覆盖、清理
# =============================================================


def test_sync_writes_one_chunk_per_entry(store: InMemoryKnowledgeStore) -> None:
    snapshot = _snapshot(
        _entry("条目甲", "正文甲", source_id="world/世界甲#1"),
        _entry("条目乙", "正文乙", source_id="world/世界甲#2"),
    )

    result = sync_worldbook_knowledge(store, snapshots=[snapshot])

    assert (result.documents, result.chunks) == (1, 2)
    assert result.scopes == {TAVERN_SCOPE: 1}
    docs = store.list_documents(TAVERN_SCOPE)
    assert len(docs) == 1
    assert docs[0].source_type == SOURCE_TYPE
    assert docs[0].chunk_count == 2


def test_chunk_text_carries_the_title(store: InMemoryKnowledgeStore) -> None:
    """标题进分块正文：BM25 靠字面命中，标题往往就是提问用的词。"""
    snapshot = _snapshot(_entry("人物设计_江浸月", "她住在城南。", source_id="world/甲#1"))

    sync_worldbook_knowledge(store, snapshots=[snapshot])

    (chunk,) = store.list_chunks(TAVERN_SCOPE)
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

    docs = store.list_documents(TAVERN_SCOPE)
    assert len(docs) == 1                    # 还是同一篇
    assert (result.documents, result.removed) == (1, 0)
    texts = [chunk.text for chunk in store.list_chunks(TAVERN_SCOPE)]
    assert any("新正文" in text for text in texts)
    assert all("旧正文" not in text for text in texts)


def test_prune_removes_documents_whose_source_is_gone(store: InMemoryKnowledgeStore) -> None:
    """★ 在酒馆里删掉的世界书不该继续被召回（否则用户会遇到「删了还在答」）。"""
    both = _snapshot(
        _entry("甲", source_id="world/甲#1"),
        _entry("乙", source_id="world/乙#1"),
    )
    sync_worldbook_knowledge(store, snapshots=[both])
    assert len(store.list_documents(TAVERN_SCOPE)) == 2

    only_one = _snapshot(_entry("甲", source_id="world/甲#1"))
    result = sync_worldbook_knowledge(store, snapshots=[only_one])

    assert result.removed == 1
    docs = store.list_documents(TAVERN_SCOPE)
    assert len(docs) == 1
    assert docs[0].title == "[酒馆] 世界书·甲（1 条）"


def test_prune_can_be_disabled(store: InMemoryKnowledgeStore) -> None:
    both = _snapshot(
        _entry("甲", source_id="world/甲#1"),
        _entry("乙", source_id="world/乙#1"),
    )
    sync_worldbook_knowledge(store, snapshots=[both])

    sync_worldbook_knowledge(
        store, snapshots=[_snapshot(_entry("甲", source_id="world/甲#1"))], prune=False
    )

    assert len(store.list_documents(TAVERN_SCOPE)) == 2


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


def test_scopes_are_kept_separate(store: InMemoryKnowledgeStore) -> None:
    """★ 两个角色的专属设定各进各的作用域，互相看不到。"""
    card_a, card_b = _card("角色甲"), _card("角色乙")
    snapshot = _snapshot(
        _entry("甲的设定", "甲的正文", source_id="char/角色甲#1", character="角色甲"),
        _entry("乙的设定", "乙的正文", source_id="char/角色乙#1", character="角色乙"),
        _entry("全局", "公共正文", source_id="world/公共#1"),
        cards=[card_a, card_b],
    )

    result = sync_worldbook_knowledge(store, snapshots=[snapshot])

    assert result.scopes == {
        tavern_scope(persona_id_for(card_a)): 1,
        tavern_scope(persona_id_for(card_b)): 1,
        TAVERN_SCOPE: 1,
    }
    titles_a = [doc.title for doc in store.list_documents(tavern_scope(persona_id_for(card_a)))]
    assert len(titles_a) == 1 and "角色内嵌设定" in titles_a[0]
    # 甲的库里没有乙的东西
    assert all("乙" not in title for title in titles_a)


def test_status_counts_every_scope(store: InMemoryKnowledgeStore) -> None:
    """状态要看**全部候选作用域**：只看全局会漏掉角色专属的那些。"""
    card = _card("角色甲")
    snapshot = _snapshot(
        _entry("全局", source_id="world/公共#1"),
        _entry("专属", source_id="char/角色甲#1", character="角色甲"),
        cards=[card],
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


# =============================================================
# 存量迁移：旧版写在共享作用域 `*` 下的全局书
# =============================================================


def test_candidate_scopes_include_shared_scope_for_legacy_cleanup() -> None:
    """★ 候选作用域必须带上 `SCOPE_ALL`，否则旧文档永远清不掉。

    旧版本把全局书写在共享作用域 `*` 下；清理只遍历「候选作用域」，
    不带 `*` 的话那些文档会一直留在共享库里，继续污染桌宠模式。
    """
    card = _card("角色甲")
    scopes = candidate_scopes([_snapshot(cards=[card])])

    assert SCOPE_ALL in scopes
    assert TAVERN_SCOPE in scopes
    assert tavern_scope(persona_id_for(card)) in scopes


def test_sync_migrates_legacy_shared_scope_documents(store: InMemoryKnowledgeStore) -> None:
    """★ 存量迁移：上次同步写在 `*` 下的全局书，这次同步会被搬到 `tavern` 并清掉旧的。

    这是「不加迁移脚本也不留脏数据」的关键——旧文档的 doc_id 由
    `(作用域, 来源文件)` 算出，作用域变了就是另一个 id，只能靠 prune 清。
    """
    snapshot = _snapshot(_entry("甲", source_id="world/甲#1"))

    # 模拟旧版本：同一篇酒馆来源文档落在共享作用域
    legacy = store.add_document(
        SCOPE_ALL,
        title="[酒馆] 世界书·甲（1 条）",
        chunks=["[甲]\n旧正文"],
        source_type=SOURCE_TYPE,
    )

    result = sync_worldbook_knowledge(store, snapshots=[snapshot])

    assert result.removed == 1                                  # 旧的那篇被清掉
    assert store.list_documents(SCOPE_ALL) == []                # 共享作用域干净了
    assert len(store.list_documents(TAVERN_SCOPE)) == 1         # 新的落在 tavern
    assert store.get_document(SCOPE_ALL, legacy.doc_id) is None
