"""写入链路测试：MemoryStore.remember_turn（事件驱动写入三层）。"""

import pytest

from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.warm.inmemory_store import InMemoryWarmStore


@pytest.fixture()
def cold(tmp_path) -> SqliteColdStore:
    return SqliteColdStore(db_path=tmp_path / "memory.db")


@pytest.fixture()
def warm() -> InMemoryWarmStore:
    return InMemoryWarmStore()


@pytest.fixture()
def store(cold, warm) -> MemoryStore:
    return MemoryStore(cold, warm)


def test_remember_turn_writes_all_layers(store: MemoryStore, cold, warm) -> None:
    stats = store.remember_turn(
        "therapist",
        "我最近总是失眠，压力很大",
        "听起来你最近很不容易……",
        turn_index=1,
        source="sess-1",
    )
    assert stats["facts"] >= 1      # 冷层事实
    assert stats["memory"] == 1     # 温层向量入库
    assert stats["summary"] == 1    # 冷层摘要增量

    assert cold.list_facts("therapist")
    assert warm.count("therapist") == 1
    summary = cold.get_summary("therapist")
    assert summary is not None and summary.scope_end == 1


def test_remember_turn_summary_accumulates(store: MemoryStore, cold) -> None:
    """摘要应滚动增量并入（参照④），而非覆盖。"""
    store.remember_turn("therapist", "我最近失眠", "…", turn_index=1)
    store.remember_turn("therapist", "今天去看了医生", "…", turn_index=2)
    summary = cold.get_summary("therapist")
    assert summary is not None
    assert summary.scope_end == 2
    assert "失眠" in summary.content and "看了医生" in summary.content


def test_remember_turn_then_recall(store: MemoryStore) -> None:
    """写入后可被后续召回（闭环验证）。"""
    store.remember_turn(
        "therapist", "我最怕打雷，会躲进被子", "…", turn_index=1
    )
    ctx = store.recall("therapist", "打雷了")
    assert ctx.memories  # 温层语义召回命中
    assert any("打雷" in r.record.text for r in ctx.memories)
    assert ctx.facts     # 冷层事实也能召回
    assert ctx.empty is False


def test_remember_turn_isolated_by_companion(store: MemoryStore, cold) -> None:
    store.remember_turn("companion_a", "我怕黑", "…", turn_index=1)
    assert cold.list_facts("companion_b") == []
    assert store.recall("companion_b", "怕黑").empty is True


def test_remember_turn_plain_text() -> None:
    """无事实可抽取的普通闲聊：仍写入温层与摘要，但不产生事实。"""
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp()) / "m.db"
    store = MemoryStore(SqliteColdStore(db_path=tmp), InMemoryWarmStore())
    stats = store.remember_turn("therapist", "嗯嗯好的", "…", turn_index=1)
    assert stats["facts"] == 0
    assert stats["memory"] == 1
    assert stats["summary"] == 1


def test_remember_turn_survives_warm_failure(cold) -> None:
    """温层不可用时冷层仍写入（降级不阻断）。"""
    from app.memory.warm.base import WarmMemoryStore

    class _BrokenWarm(WarmMemoryStore):
        def add(self, companion_id, text, **kw):
            raise RuntimeError("模拟 Qdrant 不可用")

        def search(self, companion_id, query, **kw):  # pragma: no cover
            return []

        def delete(self, companion_id, memory_id):  # pragma: no cover
            return False

        def count(self, companion_id):  # pragma: no cover
            return 0

        def list_records(self, companion_id):  # pragma: no cover - 不使用
            return []

    store = MemoryStore(cold, _BrokenWarm())
    stats = store.remember_turn("therapist", "我喜欢猫", "…", turn_index=1)
    assert stats["memory"] == 0          # 温层失败
    assert stats["facts"] >= 1           # 冷层成功
    assert cold.list_facts("therapist")
