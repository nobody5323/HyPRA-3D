"""温层内存 store 测试。"""

from datetime import datetime, timedelta

from app.memory.warm.base import CANDIDATE_FACTOR, MemoryRecord
from app.memory.warm.inmemory_store import InMemoryWarmStore


def _store() -> InMemoryWarmStore:
    return InMemoryWarmStore()


def test_add_and_count() -> None:
    store = _store()
    store.add("therapist", "小林说他喜欢下雨天")
    store.add("therapist", "小林最近在换工作")
    assert store.count("therapist") == 2


def test_search_recalls_related() -> None:
    store = _store()
    store.add("therapist", "小林说他喜欢下雨天，听着雨声很放松")
    store.add("therapist", "小林最近在换工作，压力很大")
    results = store.search("therapist", "工作压力", top_k=2)
    assert results
    assert "工作" in results[0].record.text  # 相关记忆应排最前
    assert results[0].raw_similarity > results[1].raw_similarity


def test_companion_isolation() -> None:
    store = _store()
    store.add("companion_a", "A 的独家记忆：怕黑")
    store.add("companion_b", "B 的独家记忆：恐高")
    results_a = store.search("companion_a", "独家记忆", top_k=5)
    assert len(results_a) == 1
    assert "怕黑" in results_a[0].record.text


def test_delete() -> None:
    store = _store()
    mid = store.add("therapist", "可删的记忆")
    assert store.delete("therapist", mid) is True
    assert store.count("therapist") == 0
    assert store.delete("therapist", mid) is False


def test_newer_memory_wins_with_decay() -> None:
    """同等相似度下，时间衰减让近期记忆排前（参照⑥）。"""
    store = _store()
    base = datetime(2026, 1, 1)

    # 手工注入两条相同文本的记忆，但年龄不同
    vector = store.provider.embed("小林怕打雷")
    store._collections["therapist"] = {
        "old": MemoryRecord(
            memory_id="old", companion_id="therapist",
            text="小林怕打雷", created_at=base - timedelta(days=120),
            vector=vector,
        ),
        "new": MemoryRecord(
            memory_id="new", companion_id="therapist",
            text="小林怕打雷", created_at=base - timedelta(days=1),
            vector=vector,
        ),
    }
    results = store.search("therapist", "小林怕打雷", top_k=2, now=base)
    assert results[0].record.memory_id == "new"
    # 关闭衰减后两者并列（同相似度）
    results_no_decay = store.search(
        "therapist", "小林怕打雷", top_k=2, now=base, decay_exponent=0.0
    )
    ids = [r.record.memory_id for r in results_no_decay]
    assert set(ids) == {"old", "new"}


def test_top_k_limits_results() -> None:
    store = _store()
    for i in range(10):
        store.add("therapist", f"小林今天做了第 {i} 件事")
    results = store.search("therapist", "小林今天做了事", top_k=3)
    assert len(results) == 3


def test_search_uses_candidate_pool() -> None:
    """候选池按**原始相似度**取 CANDIDATE_FACTOR × top_k 条，再施加衰减重排。

    与 QdrantWarmStore 保持同一策略。若退化为「全量参与衰减重排」，
    最新写入的第 20 条会胜出；有候选池时只有最早的 5 条参与竞争。
    """
    store = _store()
    base = datetime(2025, 1, 1)
    total = CANDIDATE_FACTOR * 4  # 20 条，远多于候选池大小
    for i in range(total):
        store.add(
            "therapist",
            "完全相同的一段文本",
            created_at=base + timedelta(days=i),
        )

    results = store.search(
        "therapist",
        "完全相同的一段文本",
        top_k=1,
        now=base + timedelta(days=total),
    )
    # 候选池 = 相似度相同的前 5 条（day 0..4），其中最新者（day 4）居首
    assert results[0].record.created_at == base + timedelta(days=CANDIDATE_FACTOR - 1)
