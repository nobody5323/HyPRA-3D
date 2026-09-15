"""M4 情绪链路集成测试：图节点、情绪加权召回、API 输出。"""

import json

import pytest

from app.graph.chat_graph import build_chat_graph
from app.graph.nodes import ChatNodes
from app.llm.base import AgentResult, ChatMessage, LLMProvider, ToolCall
from app.llm.mock import MockLLMProvider
from app.memory.cold.models import Fact, FactType
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.prompts.persona.loader import load_builtin_presets
from app.tools.emotion import EMOTION_TOOL_NAME, EmotionLabel, EmotionResult
from app.worldbook.loader import load_builtin_entries

PERSONA_ID = "therapist-elder-sister"


@pytest.fixture()
def memory(tmp_path) -> MemoryStore:
    return MemoryStore(SqliteColdStore(db_path=tmp_path / "m.db"), InMemoryWarmStore())


@pytest.fixture()
def nodes(memory: MemoryStore) -> ChatNodes:
    return ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=memory,
        llm_provider=MockLLMProvider(),
    )


@pytest.fixture()
def graph(nodes: ChatNodes):
    return build_chat_graph(nodes)


def _state(**overrides) -> dict:
    base = {
        "session_id": "s1",
        "companion_id": PERSONA_ID,
        "persona_id": PERSONA_ID,
        "user_name": "小林",
        "user_input": "我最近总是失眠，压力好大",
        "history": [],
        "turn_index": 1,
        "state_vars": {},
        "warnings": [],
    }
    base.update(overrides)
    return base


# ---------- 节点：双通道结构化输出 ----------


def test_generate_reply_structured_channel(nodes: ChatNodes) -> None:
    """mock 支持 tools → 走结构化通道，回复与情绪一并返回。"""
    out = nodes.generate_reply(_state(messages=[{"role": "user", "content": "我最近总是失眠"}]))
    assert out["reply"]
    assert out["emotion"] is not None
    assert out["emotion"].emotion == EmotionLabel.ANXIOUS
    assert out["emotion"].source == "llm"


def test_generate_reply_forces_structured_output_when_model_returns_plain_text() -> None:
    """模型只给纯文本、不调工具 → 必须强制补一次结构化输出。

    回归背景（实测缺陷）：真实模型在完整提示词下一律返回纯文本，
    旧实现直接落到关键词兜底——未命中关键词时情绪恒为 neutral / intensity 0.5，
    前端表现为「情绪强度始终 50%」。
    """
    forced_choices: list[object] = []

    class _TextOnlyProvider(MockLLMProvider):
        """chat_with_tool_loop 只给纯文本（模拟真实模型行为）。"""

        def chat_with_tool_loop(self, messages, tools, executor, **kwargs):
            return AgentResult(reply="我在听，你慢慢说。", tool_calls=[], rounds=1)

        def chat_with_tools(self, messages, tools, *, tool_choice="auto", **kwargs):
            forced_choices.append(tool_choice)
            return super().chat_with_tools(
                messages, tools, tool_choice=tool_choice, **kwargs
            )

    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=MemoryStore(SqliteColdStore(db_path=":memory:"), InMemoryWarmStore()),
        llm_provider=_TextOnlyProvider(),
    )
    out = nodes.generate_reply(
        _state(
            user_input="我最近总是失眠，压力好大",
            messages=[{"role": "user", "content": "我最近总是失眠，压力好大"}],
        )
    )

    # ① 确实发起了强制结构化调用，且指定了具体函数（而非 "auto"）
    assert forced_choices
    assert forced_choices[0] != "auto"
    assert forced_choices[0]["function"]["name"] == EMOTION_TOOL_NAME
    # ② 情绪来自结构化输出（而非关键词兜底）；回复沿用首次生成，不重复生成
    assert out["emotion"].source == "llm"
    assert out["emotion"].emotion == EmotionLabel.ANXIOUS
    assert out["reply"] == "我在听，你慢慢说。"


def test_generate_reply_fallback_channel() -> None:
    """模型不支持 tools（chat_with_tools 返回 None）→ 降级到 chat + 正则兜底。"""

    class _NoToolsProvider(LLMProvider):
        name = "no-tools"

        def chat(self, messages, *, temperature=0.7, max_tokens=None) -> str:
            return "我在这儿听着。"

        # 不覆盖 chat_with_tools → 默认返回 None

    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=MemoryStore(SqliteColdStore(db_path=":memory:"), InMemoryWarmStore()),
        llm_provider=_NoToolsProvider(),
    )
    out = nodes.generate_reply(_state(messages=[{"role": "user", "content": "我很焦虑"}]))
    assert out["reply"] == "我在这儿听着。"   # 用真实回复，而非兜底占位
    assert out["emotion"].source == "fallback"
    assert out["emotion"].emotion == EmotionLabel.ANXIOUS


def test_generate_reply_malformed_tool_call_falls_back() -> None:
    """工具调用参数非法 → 降级，不抛异常。"""

    class _BadArgsProvider(LLMProvider):
        name = "bad-args"

        def chat(self, messages, *, temperature=0.7, max_tokens=None) -> str:
            return "我在。"

        def chat_with_tools(self, messages, tools, *, tool_choice="auto", temperature=0.7):
            return [ToolCall(name=EMOTION_TOOL_NAME, arguments="{坏 json")]

    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=MemoryStore(SqliteColdStore(db_path=":memory:"), InMemoryWarmStore()),
        llm_provider=_BadArgsProvider(),
    )
    out = nodes.generate_reply(_state(messages=[{"role": "user", "content": "我好累"}]))
    assert out["reply"] == "我在。"
    assert out["emotion"].source == "fallback"


# ---------- 图端到端 ----------


def test_graph_produces_emotion(graph) -> None:
    result = graph.invoke(_state())
    assert result["emotion"] is not None
    assert result["emotion"].emotion == EmotionLabel.ANXIOUS
    assert result["emotion"].label_zh == "焦虑"
    assert result["emotion"].facial_expression  # 供 M5 数字人使用


def test_graph_emotion_written_to_facts(graph, memory: MemoryStore) -> None:
    """本轮情绪应作为 emotion_tag 落到冷层事实。"""
    graph.invoke(_state(user_input="我最近总是失眠，很焦虑"))
    facts = memory.cold.list_facts(PERSONA_ID)
    assert facts
    assert any(f.emotion_tag == "anxious" for f in facts)


def test_graph_emotion_written_to_warm_metadata(graph, memory: MemoryStore) -> None:
    """温层向量 metadata 应带情绪标签（供后续加权）。"""
    graph.invoke(_state(user_input="我最近总是失眠"))
    results = memory.warm.search(PERSONA_ID, "失眠", top_k=5)
    assert results
    assert results[0].record.metadata.get("emotion") == "anxious"


# ---------- 情绪加权召回（参照⑤） ----------


def test_recall_boosts_same_emotion_memory(memory: MemoryStore) -> None:
    """相同语义下，情绪标签一致的记忆应被加权提前。"""
    memory.warm.add("c1", "今天天气不错", metadata={"emotion": "happy"})
    memory.warm.add("c1", "今天天气不错", metadata={"emotion": "anxious"})

    without = memory.recall("c1", "今天天气不错")
    with_emotion = memory.recall("c1", "今天天气不错", emotion="anxious")

    # 加权后，anxious 那条排到首位
    assert with_emotion.memories[0].record.metadata["emotion"] == "anxious"
    # 加权分高于未加权时的同名条目分数
    boosted = next(
        r for r in with_emotion.memories if r.record.metadata["emotion"] == "anxious"
    )
    plain = next(
        r for r in without.memories if r.record.metadata["emotion"] == "anxious"
    )
    assert boosted.score > plain.score


def test_recall_boosts_same_emotion_facts(memory: MemoryStore) -> None:
    """冷层：同情绪事实排在前面（即便 importance 略低）。"""
    memory.cold.save_fact(
        "c1",
        Fact(type=FactType.EVENT, subject="小林", predicate="提到", object="琐事",
             importance=3, emotion_tag="happy"),
    )
    memory.cold.save_fact(
        "c1",
        Fact(type=FactType.EMOTION_PATTERN, subject="小林", predicate="提到", object="压力",
             importance=5, emotion_tag="anxious"),
    )
    ctx = memory.recall("c1", "聊聊", emotion="happy")
    assert ctx.facts[0].emotion_tag == "happy"   # 同情绪优先于更高 importance


def test_recall_without_emotion_keeps_importance_order(memory: MemoryStore) -> None:
    memory.cold.save_fact(
        "c1", Fact(type=FactType.EVENT, subject="s", predicate="p", object="低", importance=2)
    )
    memory.cold.save_fact(
        "c1", Fact(type=FactType.EVENT, subject="s", predicate="p", object="高", importance=5)
    )
    ctx = memory.recall("c1", "聊聊")
    assert ctx.facts[0].object == "高"


def test_remember_turn_accepts_emotion(memory: MemoryStore) -> None:
    stats = memory.remember_turn(
        "c1", "我很焦虑", "我在", turn_index=1, emotion="anxious"
    )
    assert stats["memory"] == 1
    facts = memory.cold.list_facts("c1")
    assert all(f.emotion_tag == "anxious" for f in facts) if facts else True
