"""回复后事件驱动抽取器测试（规则版，无 LLM 依赖）。"""

import pytest

from app.llm.mock import MockLLMProvider
from app.memory.cold.extractor import (
    LLMExtractor,
    RuleBasedExtractor,
    create_extractor,
)
from app.memory.cold.models import FactType


@pytest.fixture()
def extractor() -> RuleBasedExtractor:
    return RuleBasedExtractor()


def _types(result) -> set[FactType]:
    return {f.type for f in result.facts}


def test_extract_preference(extractor: RuleBasedExtractor) -> None:
    result = extractor.extract("我很喜欢下雨天", "…", companion_id="c")
    assert FactType.PREFERENCE in _types(result)
    pref = next(f for f in result.facts if f.type == FactType.PREFERENCE)
    assert pref.predicate == "喜欢"
    assert "下雨天" in pref.object


def test_extract_emotion_pattern(extractor: RuleBasedExtractor) -> None:
    result = extractor.extract("最近总是失眠，压力好大", "…", companion_id="c")
    assert FactType.EMOTION_PATTERN in _types(result)


def test_extract_relationship(extractor: RuleBasedExtractor) -> None:
    result = extractor.extract("我妈最近身体不好", "…", companion_id="c")
    assert FactType.RELATIONSHIP in _types(result)


def test_extract_time_anchor(extractor: RuleBasedExtractor) -> None:
    result = extractor.extract("上周换了新工作", "…", companion_id="c")
    facts = [f for f in result.facts if f.occurred_at]
    assert facts
    assert facts[0].occurred_at == "上周"


def test_importance_by_type(extractor: RuleBasedExtractor) -> None:
    """关系/情绪模式类应给高重要性（>=4，长期锚点）。"""
    result = extractor.extract("我妈身体不好，我很焦虑", "…", companion_id="c")
    for fact in result.facts:
        if fact.type in {FactType.RELATIONSHIP, FactType.EMOTION_PATTERN}:
            assert fact.importance >= 4


def test_rule_confidence_conservative(extractor: RuleBasedExtractor) -> None:
    """规则版置信度应保守（0.6，低于 LLM 版 0.8）。"""
    result = extractor.extract("我喜欢猫", "…", companion_id="c")
    assert all(f.confidence == 0.6 for f in result.facts)


def test_dedup_same_turn(extractor: RuleBasedExtractor) -> None:
    """同轮重复表述不应产生重复事实。"""
    result = extractor.extract("我喜欢猫，我真的很喜欢猫", "…", companion_id="c")
    keys = [(f.type, f.predicate, f.object) for f in result.facts]
    assert len(keys) == len(set(keys))


def test_no_fact_for_plain_text(extractor: RuleBasedExtractor) -> None:
    result = extractor.extract("嗯嗯，好的", "…", companion_id="c")
    assert result.facts == []


def test_source_recorded(extractor: RuleBasedExtractor) -> None:
    result = extractor.extract("我喜欢猫", "…", companion_id="c", source="sess-1")
    assert all(f.source == "sess-1" for f in result.facts)


def test_factory_rule() -> None:
    assert isinstance(create_extractor("rule"), RuleBasedExtractor)
    assert isinstance(create_extractor(""), RuleBasedExtractor)


def test_rule_extractor_touches_identical_fact(
    extractor: RuleBasedExtractor,
) -> None:
    """与已有事实三元组完全相同时不新增，改为 touch（零成本去重）。

    这一步刻意交给规则而非 LLM：完全一致的三元组无需语义理解，
    规则判定零成本、零误判、可单测。
    """
    from app.memory.cold.models import Fact

    existing = Fact(
        type=FactType.PREFERENCE,
        subject="用户",
        predicate="喜欢",
        object="下雨天",
    )
    existing.fact_id = "f1"

    result = extractor.extract(
        "我喜欢下雨天", "…", companion_id="c", known_facts=[existing]
    )

    assert not any(f.object == "下雨天" for f in result.facts)
    assert len(result.updates) == 1
    assert result.updates[0].fact_id == "f1"
    assert result.updates[0].action == "touch"


def test_rule_extractor_without_known_facts_still_adds(
    extractor: RuleBasedExtractor,
) -> None:
    """没有已知事实时仍正常新增。"""
    result = extractor.extract("我喜欢下雨天", "…", companion_id="c")
    assert any(f.object == "下雨天" for f in result.facts)
    assert result.updates == []


def test_factory_llm_without_provider_falls_back() -> None:
    """未提供 provider 时退回规则版（而非抛错阻断启动）。"""
    assert isinstance(create_extractor("llm"), RuleBasedExtractor)


def test_factory_llm_with_provider() -> None:
    extractor = create_extractor("llm", llm_provider=MockLLMProvider())
    assert isinstance(extractor, LLMExtractor)
    assert extractor.name == "llm"


def test_factory_unknown_raises() -> None:
    with pytest.raises(ValueError):
        create_extractor("magic")
