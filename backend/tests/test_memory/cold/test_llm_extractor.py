"""LLM 抽取器测试（假 provider，无网络）。"""

import json

from app.llm.base import ChatMessage, LLMProvider
from app.memory.cold.extractor import (
    FactUpdate,
    LLMExtractor,
    RuleBasedExtractor,
)
from app.memory.cold.models import Fact, FactType


class _ScriptedProvider(LLMProvider):
    """按脚本返回文本的假 provider（可模拟失败）。"""

    name = "scripted"

    def __init__(self, reply: str = "", *, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[list[ChatMessage]] = []

    def chat(self, messages: list[ChatMessage], **kwargs) -> str:  # noqa: ARG002
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return self.reply


def _payload(facts: list[dict] | None = None, updates: list[dict] | None = None) -> str:
    return json.dumps({"facts": facts or [], "updates": updates or []}, ensure_ascii=False)


def _known_fact(fact_id: str = "f1", **overrides) -> Fact:
    base = {
        "type": FactType.RELATIONSHIP,
        "subject": "用户",
        "predicate": "在…工作",
        "object": "A 公司",
        "importance": 4,
    }
    base.update(overrides)
    fact = Fact(**base)
    fact.fact_id = fact_id
    return fact


# ---------- 正常解析 ----------


def test_extracts_facts_with_content_importance() -> None:
    """LLM 按内容给出的 importance 应被采用（规则版只能按类型给常量）。"""
    provider = _ScriptedProvider(
        _payload(
            facts=[
                {
                    "type": "relationship",
                    "subject": "用户",
                    "predicate": "有",
                    "object": "母亲患高血压",
                    "importance": 5,
                }
            ]
        )
    )
    result = LLMExtractor(provider).extract("我妈有高血压", "…", companion_id="c")

    assert len(result.facts) == 1
    fact = result.facts[0]
    assert fact.object == "母亲患高血压"
    assert fact.importance == 5
    assert fact.confidence == 0.8          # LLM 版高于规则版的 0.6
    assert fact.source == ""


def test_parses_updates_for_known_facts() -> None:
    """updates 指令应被解析（这是事实状态机唯一的触发来源）。"""
    provider = _ScriptedProvider(
        _payload(updates=[{"fact_id": "f1", "action": "stale", "reason": "换工作了"}])
    )
    result = LLMExtractor(provider).extract(
        "我换到 B 公司了", "…", companion_id="c", known_facts=[_known_fact("f1")]
    )

    assert result.updates == [
        FactUpdate(fact_id="f1", action="stale", reason="换工作了")
    ]


def test_prompt_includes_known_facts() -> None:
    """已有事实必须进入 prompt，否则模型无法判断冲突（会被下面的断言抓到）。"""
    provider = _ScriptedProvider(_payload())
    LLMExtractor(provider).extract(
        "随便说点什么", "…", companion_id="c", known_facts=[_known_fact("f1")]
    )

    prompt = provider.calls[0][-1].content
    assert "f1" in prompt
    assert "A 公司" in prompt


def test_empty_payload_is_ok() -> None:
    """没有可抽内容时返回空结果，而不是报错。"""
    result = LLMExtractor(_ScriptedProvider(_payload())).extract(
        "今天天气不错", "…", companion_id="c"
    )
    assert result.facts == []
    assert result.updates == []


# ---------- 健壮性 ----------


def test_parses_markdown_fenced_json() -> None:
    """模型常把 JSON 包在 ```json 代码块里，必须能解析。"""
    fenced = "```json\n" + _payload(facts=[
        {"type": "preference", "object": "下雨天", "importance": 3}
    ]) + "\n```"
    result = LLMExtractor(_ScriptedProvider(fenced)).extract(
        "我喜欢下雨天", "…", companion_id="c"
    )
    assert len(result.facts) == 1


def test_invalid_json_falls_back_to_rules() -> None:
    """模型输出不是 JSON 时降级到规则版，不抛异常。"""
    extractor = LLMExtractor(_ScriptedProvider("抱歉，我不太确定。"))
    result = extractor.extract("我很喜欢下雨天", "…", companion_id="c")

    assert any(f.object == "下雨天" for f in result.facts)   # 规则版抽到了


def test_provider_error_falls_back_to_rules() -> None:
    extractor = LLMExtractor(_ScriptedProvider(error=RuntimeError("网络超时")))
    result = extractor.extract("我很喜欢下雨天", "…", companion_id="c")
    assert any(f.object == "下雨天" for f in result.facts)


def test_malformed_fact_item_is_skipped() -> None:
    """单条格式不合法只跳过该条，不影响其余条目。"""
    provider = _ScriptedProvider(
        _payload(
            facts=[
                {"type": "preference", "object": "", "importance": 3},      # 缺 object
                {"type": "不存在的类型", "object": "X", "importance": 3},   # 非法枚举
                {"type": "preference", "object": "下雨天", "importance": 3},
            ]
        )
    )
    result = LLMExtractor(provider).extract("…", "…", companion_id="c")
    assert [f.object for f in result.facts] == ["下雨天"]


def test_hallucinated_fact_id_is_dropped() -> None:
    """fact_id 必须来自已给出的事实列表——防模型编造 id 去改无关条目。"""
    provider = _ScriptedProvider(
        _payload(updates=[
            {"fact_id": "编造的-id", "action": "stale"},
            {"fact_id": "f1", "action": "touch"},
        ])
    )
    result = LLMExtractor(provider).extract(
        "…", "…", companion_id="c", known_facts=[_known_fact("f1")]
    )
    assert [u.fact_id for u in result.updates] == ["f1"]


def test_invalid_action_is_dropped() -> None:
    provider = _ScriptedProvider(
        _payload(updates=[{"fact_id": "f1", "action": "删除"}])
    )
    result = LLMExtractor(provider).extract(
        "…", "…", companion_id="c", known_facts=[_known_fact("f1")]
    )
    assert result.updates == []


def test_importance_is_clamped() -> None:
    """importance 越界应被夹到 [1, 5]，而不是让 pydantic 抛错丢失整条。"""
    provider = _ScriptedProvider(
        _payload(facts=[{"type": "other", "object": "X", "importance": 99}])
    )
    result = LLMExtractor(provider).extract("…", "…", companion_id="c")
    assert result.facts[0].importance == 5


def test_fallback_defaults_to_rule_extractor() -> None:
    """未显式指定时 fallback 是规则版。"""
    assert isinstance(LLMExtractor(_ScriptedProvider()).fallback, RuleBasedExtractor)
