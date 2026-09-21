"""冷层 SQLite 存储测试（使用临时文件，不污染真实 data 目录）。"""

import uuid
from datetime import datetime, timedelta

import pytest

from app.memory.cold.models import Fact, FactStatus, FactType
from app.memory.cold.sqlite_store import SqliteColdStore


@pytest.fixture()
def store(tmp_path) -> SqliteColdStore:
    """每个测试用独立临时库文件。"""
    return SqliteColdStore(db_path=tmp_path / "test_memory.db")


def _fact(**overrides) -> Fact:
    base = {
        "type": FactType.PREFERENCE,
        "subject": "小林",
        "predicate": "喜欢",
        "object": "下雨天",
        "importance": 3,
    }
    base.update(overrides)
    return Fact(**base)


def test_save_and_get(store: SqliteColdStore) -> None:
    fact = _fact()
    fact_id = store.save_fact("therapist", fact)
    got = store.get_fact("therapist", fact_id)
    assert got is not None
    assert got.fact_id == fact_id
    assert got.subject == "小林"
    assert got.predicate == "喜欢"
    assert got.object == "下雨天"
    assert got.type == FactType.PREFERENCE


def test_save_generates_fact_id(store: SqliteColdStore) -> None:
    fact_id = store.save_fact("therapist", _fact())
    assert fact_id  # 自动生成的 uuid hex 非空


def test_list_facts_by_type_and_status(store: SqliteColdStore) -> None:
    store.save_fact("therapist", _fact(type=FactType.PREFERENCE, object="猫"))
    store.save_fact("therapist", _fact(type=FactType.EVENT, object="换工作"))

    prefs = store.list_facts("therapist", type=FactType.PREFERENCE)
    assert len(prefs) == 1
    assert prefs[0].object == "猫"

    events = store.list_facts("therapist", type=FactType.EVENT)
    assert len(events) == 1
    assert events[0].object == "换工作"


def test_status_filters_active_by_default(store: SqliteColdStore) -> None:
    active_id = store.save_fact("therapist", _fact(object="晴天"))
    resolved_id = store.save_fact(
        "therapist", _fact(object="旧事", status=FactStatus.RESOLVED)
    )
    active = store.list_facts("therapist")
    assert {f.fact_id for f in active} == {active_id}
    assert resolved_id not in {f.fact_id for f in active}


def test_update_status_and_touch(store: SqliteColdStore) -> None:
    fact_id = store.save_fact("therapist", _fact(object="项目"))
    # 状态流转：active → resolved
    assert store.update_status("therapist", fact_id, FactStatus.RESOLVED) is True
    got = store.get_fact("therapist", fact_id)
    assert got is not None and got.status == FactStatus.RESOLVED
    # touch 刷新 last_seen_at
    assert got.last_seen_at is None
    assert store.touch("therapist", fact_id) is True
    assert store.get_fact("therapist", fact_id).last_seen_at is not None
    # 不存在的 id 操作返回 False
    assert store.update_status("therapist", "nope", FactStatus.STALE) is False
    assert store.touch("therapist", "nope") is False


def test_delete_fact(store: SqliteColdStore) -> None:
    fact_id = store.save_fact("therapist", _fact(object="可删"))
    assert store.delete_fact("therapist", fact_id) is True
    assert store.get_fact("therapist", fact_id) is None
    assert store.delete_fact("therapist", fact_id) is False


def test_companion_isolation(store: SqliteColdStore) -> None:
    """不同陪伴对象（角色）之间互不可见。"""
    a_id = store.save_fact("companion_a", _fact(object="A 的秘密"))
    store.save_fact("companion_b", _fact(object="B 的秘密"))
    facts_a = store.list_facts("companion_a")
    assert [f.fact_id for f in facts_a] == [a_id]
    assert all(f.object == "A 的秘密" for f in facts_a)


def test_kebab_case_companion_id(store: SqliteColdStore) -> None:
    """回归：kebab-case 的陪伴对象 id（如 therapist-elder-sister）必须可用。

    真实 chat 流程以 persona.id 作为 companion_id，含连字符；
    早期实现会因表名校验拒绝而静默写入失败。
    """
    cid = "therapist-elder-sister"
    store.save_fact(cid, _fact(object="猫"))
    assert len(store.list_facts(cid)) == 1

    store.append_summary(cid, scope_end=3, new_content="摘要内容")
    summary = store.get_summary(cid)
    assert summary is not None and summary.scope_end == 3

    # 与相似命名的其它对象不混淆
    assert store.list_facts("therapist_elder_sister") == []


def test_summary_append_incremental(store: SqliteColdStore) -> None:
    """摘要应滚动增量并入，而非覆盖。"""
    assert store.get_summary("therapist") is None
    s1 = store.append_summary("therapist", scope_end=10, new_content="小林提到工作压力大。")
    assert s1.content == "小林提到工作压力大。"
    assert s1.scope_end == 10

    s2 = store.append_summary("therapist", scope_end=20, new_content="后来开始养猫缓解压力。")
    assert "工作压力大" in s2.content
    assert "养猫缓解压力" in s2.content
    assert s2.scope_end == 20
    assert s2.scope_start == s1.scope_start  # 起点保持


def test_summary_isolated_by_companion(store: SqliteColdStore) -> None:
    store.append_summary("therapist", scope_end=5, new_content="甲摘要")
    assert store.get_summary("other") is None
    other = store.append_summary("other", scope_end=3, new_content="乙摘要")
    assert "甲摘要" not in other.content


def test_invalid_companion_id_rejected(store: SqliteColdStore) -> None:
    with pytest.raises(ValueError):
        store.save_fact("坏 id;-", _fact())
    with pytest.raises(ValueError):
        store.list_facts("坏 id;-")


def test_anchor_property() -> None:
    assert _fact(importance=5).anchor is True
    assert _fact(importance=2).anchor is False


def test_summary_text_render() -> None:
    fact = _fact(occurred_at="上周三")
    assert fact.summary_text == "小林 喜欢 下雨天 （上周三）"


def test_list_facts_importance_wins_over_recency(store: SqliteColdStore) -> None:
    """高 importance 但创建较早的事实不能被时间截断排除（P1 回归测试）。

    回归点：旧实现是 `ORDER BY created_at DESC LIMIT n`，再在应用层按
    importance 排序——「先截断、后排序」会让重要但不新的事实永远进不了召回。
    """
    now = datetime.now()
    store.save_fact(
        "therapist",
        _fact(object="母亲患高血压", importance=5, created_at=now - timedelta(days=100)),
    )
    # 填充足够多的新事实，把 limit 名额占满
    for i in range(10):
        store.save_fact(
            "therapist",
            _fact(object=f"闲聊细节{i}", importance=2, created_at=now - timedelta(days=i)),
        )

    facts = store.list_facts("therapist", limit=5)
    assert len(facts) == 5
    assert facts[0].object == "母亲患高血压"
