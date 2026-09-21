"""记忆门面（MemoryStore）测试：三层聚合、拼接顺序、预算、降级。"""

from datetime import datetime, timedelta

import pytest

from app.memory.cold.extractor import (
    ExtractionResult,
    FactUpdate,
    TurnExtractor,
)
from app.memory.cold.models import Fact, FactStatus, FactType
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryContext, MemoryStore
from app.memory.warm.base import SearchResult, WarmMemoryStore
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


def _fact(**overrides) -> Fact:
    base = {
        "type": FactType.PREFERENCE,
        "subject": "小林",
        "predicate": "喜欢",
        "object": "下雨天",
    }
    base.update(overrides)
    return Fact(**base)


class _BrokenWarmStore(WarmMemoryStore):
    """模拟 Qdrant 不可用（连接失败）。"""

    def add(self, companion_id, text, **kw):  # pragma: no cover - 不使用
        raise RuntimeError("模拟连接失败")

    def search(self, companion_id, query, **kw):
        raise RuntimeError("模拟 Qdrant 连接失败")

    def delete(self, companion_id, memory_id):  # pragma: no cover
        return False

    def count(self, companion_id):  # pragma: no cover
        return 0

    def list_records(self, companion_id):  # pragma: no cover - 不使用
        raise RuntimeError("模拟 Qdrant 连接失败")


# ---------- 召回聚合 ----------


def test_recall_empty(store: MemoryStore) -> None:
    ctx = store.recall("therapist", "随便聊聊")
    assert ctx.empty is True


def test_recall_aggregates_layers(cold, warm) -> None:
    warm.add("therapist", "小林说他喜欢下雨天，听着雨声很放松")
    cold.save_fact("therapist", _fact(object="猫"))

    store = MemoryStore(cold, warm)
    ctx = store.recall("therapist", "下雨")

    assert len(ctx.memories) >= 1
    assert len(ctx.facts) == 1
    assert ctx.empty is False


def test_facts_sorted_by_importance(cold, warm) -> None:
    cold.save_fact("therapist", _fact(object="小细节", importance=1))
    cold.save_fact("therapist", _fact(object="核心事实", importance=5))
    store = MemoryStore(cold, warm)

    ctx = store.recall("therapist", "小林")
    assert ctx.facts[0].object == "核心事实"  # 高 importance 优先


def test_companion_isolation_via_store(cold, warm) -> None:
    warm.add("companion_a", "A 的独家记忆：怕黑")
    cold.save_fact("companion_a", _fact(object="A 的猫"))
    store = MemoryStore(cold, warm)

    ctx_a = store.recall("companion_a", "独家记忆")
    ctx_b = store.recall("companion_b", "独家记忆")
    assert ctx_a.memories and ctx_a.facts
    assert ctx_b.empty is True


# ---------- 配置接线（P3）----------


class _SpyWarmStore(WarmMemoryStore):
    """记录 search 收到的参数（验证配置是否真正传到温层）。"""

    def __init__(self) -> None:
        self.search_kwargs: dict = {}

    def add(self, companion_id, text, **kw):  # pragma: no cover - 不使用
        return ""

    def search(self, companion_id, query, **kw):
        self.search_kwargs = kw
        return []

    def delete(self, companion_id, memory_id):  # pragma: no cover
        return False

    def count(self, companion_id):  # pragma: no cover
        return 0

    def list_records(self, companion_id):  # pragma: no cover - 不使用
        return []


def test_recall_uses_candidate_pool_without_pre_decay(cold) -> None:
    """稠密通道取候选时**不施加衰减**，且候选数为 candidate_n。

    衰减必须移到 RRF 融合之后：若在候选阶段就衰减，旧记忆会在进入融合前
    被滤掉，导致 BM25 无法把它救回来。
    """
    spy = _SpyWarmStore()
    store = MemoryStore(cold, spy, candidate_n=7)
    store.recall("therapist", "下雨")

    assert spy.search_kwargs["top_k"] == 7
    assert spy.search_kwargs["decay_exponent"] == 0.0


def test_half_life_affects_final_score(cold, warm) -> None:
    """半衰期参数应作用于融合后的分数：老记忆在短半衰期下得分更低（P3）。"""
    warm.add(
        "therapist",
        "小林喜欢下雨天",
        created_at=datetime.now() - timedelta(days=100),
    )

    short = MemoryStore(cold, warm, half_life_days=10.0).recall("therapist", "下雨天")
    long = MemoryStore(cold, warm, half_life_days=3650.0).recall("therapist", "下雨天")

    assert short.memories and long.memories
    assert long.memories[0].score > short.memories[0].score


def test_emotion_boost_is_configurable(cold, warm) -> None:
    """同情绪记忆的加权系数应可配置（默认 1.25），放大后得分更高。"""
    warm.add("therapist", "小林说下雨天让他很安心", metadata={"emotion": "calm"})

    neutral = MemoryStore(cold, warm, emotion_boost=1.0).recall(
        "therapist", "下雨天", emotion="calm"
    )
    boosted = MemoryStore(cold, warm, emotion_boost=10.0).recall(
        "therapist", "下雨天", emotion="calm"
    )

    assert neutral.memories and boosted.memories
    assert boosted.memories[0].score > neutral.memories[0].score


# ---------- 事实状态机（步骤 ④：接通 update_status / touch）----------


class _ScriptedExtractor(TurnExtractor):
    """返回预设抽取结果的假抽取器，并记录收到的 known_facts。"""

    name = "scripted"

    def __init__(self, result: ExtractionResult) -> None:
        self.result = result
        self.seen_known: list[Fact] = []

    def extract(
        self,
        user_text: str,
        assistant_text: str,
        *,
        companion_id: str,
        source: str = "",
        subject: str = "用户",
        known_facts: list[Fact] | None = None,
    ) -> ExtractionResult:
        self.seen_known = list(known_facts or [])
        return self.result


def test_stale_update_removes_fact_from_recall(cold, warm) -> None:
    """stale 指令应真正改掉事实状态——这是状态机唯一的触发入口。"""
    fact_id = cold.save_fact("therapist", _fact(object="在 A 公司工作"))
    extractor = _ScriptedExtractor(
        ExtractionResult(updates=[FactUpdate(fact_id=fact_id, action="stale")])
    )
    store = MemoryStore(cold, warm, extractor=extractor)

    stats = store.remember_turn("therapist", "我换到 B 公司了", "…", turn_index=1)

    assert stats["updates"] == 1
    assert cold.list_facts("therapist") == []                    # 退出召回
    assert cold.list_facts("therapist", status=FactStatus.STALE)  # 但记录保留


def test_resolved_update_sets_status(cold, warm) -> None:
    """resolved 表示事件闭环（不是被否定，而是有结局了）。"""
    fact_id = cold.save_fact("therapist", _fact(object="打算和老板谈加薪"))
    extractor = _ScriptedExtractor(
        ExtractionResult(updates=[FactUpdate(fact_id=fact_id, action="resolved")])
    )
    MemoryStore(cold, warm, extractor=extractor).remember_turn(
        "therapist", "加薪的事谈成了", "…", turn_index=1
    )

    assert cold.list_facts("therapist") == []
    assert cold.list_facts("therapist", status=FactStatus.RESOLVED)


def test_touch_refreshes_and_boosts_confidence(cold, warm) -> None:
    """touch 应刷新 last_seen_at 并提升 confidence（反复印证更可信）。"""
    fact_id = cold.save_fact("therapist", _fact(object="喜欢下雨天"))
    before = cold.get_fact("therapist", fact_id)
    assert before is not None and before.last_seen_at is None

    extractor = _ScriptedExtractor(
        ExtractionResult(updates=[FactUpdate(fact_id=fact_id, action="touch")])
    )
    MemoryStore(cold, warm, extractor=extractor).remember_turn(
        "therapist", "我还是喜欢下雨天", "…", turn_index=1
    )

    after = cold.get_fact("therapist", fact_id)
    assert after is not None
    assert after.last_seen_at is not None
    assert after.confidence > before.confidence


def test_extractor_receives_related_known_facts(cold, warm) -> None:
    """抽取器应收到与本轮相关的已有事实（否则判断不了语义冲突）。"""
    cold.save_fact("therapist", _fact(predicate="在…工作", object="A公司", importance=4))
    cold.save_fact("therapist", _fact(predicate="喜欢", object="下雨天", importance=2))

    extractor = _ScriptedExtractor(ExtractionResult())
    MemoryStore(cold, warm, extractor=extractor).remember_turn(
        "therapist", "A公司那边最近怎么样", "…", turn_index=1
    )

    assert extractor.seen_known
    assert any("A公司" in fact.object for fact in extractor.seen_known)


def test_update_failure_does_not_block_writes(cold, warm) -> None:
    """指向不存在 fact_id 的更新指令应静默失败，不影响本轮其它写入。"""
    extractor = _ScriptedExtractor(
        ExtractionResult(updates=[FactUpdate(fact_id="不存在", action="stale")])
    )
    store = MemoryStore(cold, warm, extractor=extractor)

    stats = store.remember_turn("therapist", "我最近总是失眠", "…", turn_index=1)
    assert stats["updates"] == 0     # 失败被吞掉
    assert stats["memory"] == 1      # 情景记忆仍写入


# ---------- 容错降级 ----------


def test_warm_failure_degrades_gracefully(cold) -> None:
    """温层不可用时：仍返回冷层内容，并记 warning（不抛异常）。"""
    cold.save_fact("therapist", _fact(object="猫"))
    store = MemoryStore(cold, _BrokenWarmStore())

    ctx = store.recall("therapist", "猫")
    assert ctx.facts  # 冷层仍可用
    assert ctx.memories == []
    assert any("情景记忆召回失败" in w for w in ctx.warnings)


def test_memory_context_empty_flag() -> None:
    assert MemoryContext().empty is True
    assert MemoryContext(facts=[_fact()]).empty is False
