"""按作用域清空记忆（`DELETE /chat/memory` 的数据层保证）。

最贵的错误是「清多了」：用户要忘掉的是**一个角色**，不是全部角色。
所以每个用例都断言「另一个角色一字未动」。

另一处容易漏的是**进程内缓存**：温层/冷层的持久化删了，但按全量记录建出来的
BM25 稀疏索引还在内存里，清完仍会召回已删的记忆（现场就是「清空了，它还提」）。
"""

from app.memory.cold.models import Fact, FactType
from app.memory.cold.mood_log import MoodLogEntry, SqliteMoodLogStore
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.store import MemoryStore
from app.memory.warm.inmemory_store import InMemoryWarmStore

A = "persona-a"
B = "persona-b"


def _fact(text: str) -> Fact:
    return Fact(type=FactType.OTHER, subject="用户", predicate="喜欢", object=text)


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(
        cold=SqliteColdStore(db_path=tmp_path / "memory.db"),
        warm=InMemoryWarmStore(),
    )


def test_purge_scope_clears_only_that_companion(tmp_path) -> None:
    store = _store(tmp_path)
    store.warm.add(A, "甲的回忆一")
    store.warm.add(A, "甲的回忆二")
    store.cold.save_fact(A, _fact("下雨天"))
    store.warm.add(B, "乙的回忆")
    store.cold.save_fact(B, _fact("喝咖啡"))

    counts = store.purge_scope(A)

    assert counts == {"warm": 2, "facts": 1}
    assert store.warm.count(A) == 0
    assert store.cold.list_facts(A) == []
    # 另一个角色一字未动
    assert store.warm.count(B) == 1
    assert len(store.cold.list_facts(B)) == 1


def test_purge_scope_drops_sparse_index_cache(tmp_path) -> None:
    """清空后 BM25 索引缓存必须失效，否则仍会召回已删的记忆。"""
    store = _store(tmp_path)
    store.warm.add(A, "甲说过他最近总是失眠")
    store._lexical_view(A)  # 触发惰性构建（等价于首次召回时发生的事）
    assert A in store._lexical

    store.purge_scope(A)

    assert A not in store._lexical
    # 清完之后再召回，该角色不再有任何记忆
    assert store.recall(A, "失眠").memories == []


def test_purge_scope_is_idempotent(tmp_path) -> None:
    """没有数据时返回 0，不报错（清空一个已经空的库不是错误）。"""
    store = _store(tmp_path)

    assert store.purge_scope(A) == {"warm": 0, "facts": 0}
    assert store.purge_scope(A) == {"warm": 0, "facts": 0}


def test_mood_log_clear_scope(tmp_path) -> None:
    log = SqliteMoodLogStore(db_path=tmp_path / "memory.db")
    log.add(MoodLogEntry(companion_id=A, emotion="anxious"))
    log.add(MoodLogEntry(companion_id=A, emotion="tired"))
    log.add(MoodLogEntry(companion_id=B, emotion="happy"))

    assert log.clear_scope(A) == 2
    assert log.list_recent(A) == []
    assert len(log.list_recent(B)) == 1
    assert log.clear_scope(A) == 0  # 幂等


def test_knowledge_clear_scope() -> None:
    store = InMemoryKnowledgeStore()
    store.add_document(A, title="甲的资料", chunks=["一段", "二段"])
    store.add_document(B, title="乙的资料", chunks=["一段"])

    assert store.clear_scope(A) == 2
    assert store.count_chunks(A) == 0
    assert store.list_documents(A) == []
    # 别的角色的知识库不受影响
    assert store.count_chunks(B) == 1
    assert store.clear_scope(A) == 0  # 幂等
