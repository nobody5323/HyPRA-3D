"""LangGraph 编排测试：节点行为 + 图端到端。"""

import pytest

from app.graph.chat_graph import (
    NODE_SEQUENCE,
    build_chat_graph,
)
from app.graph.nodes import ChatNodes
from app.llm.mock import MockLLMProvider
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.knowledge.retriever import KnowledgeRetriever
from app.memory.store import MemoryStore
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.prompts.persona.loader import load_builtin_presets
from app.rag.prompt_manager import PromptManager
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
        prompt_manager=PromptManager(),
    )


@pytest.fixture()
def graph(nodes: ChatNodes):
    return build_chat_graph(nodes)


def _state(**overrides) -> dict:
    base = {
        "session_id": "sess-1",
        "companion_id": PERSONA_ID,
        "persona_id": PERSONA_ID,
        "user_name": "小林",
        "user_input": "我最近总是失眠",
        "history": [],
        "turn_index": 1,
        "state_vars": {"current_mood": "低落"},
    }
    base.update(overrides)
    return base


# ---------- 节点单测 ----------


def test_load_persona_replaces_state_vars(nodes: ChatNodes) -> None:
    out = nodes.load_persona(_state())
    assert "小林" in out["persona_text"]
    assert "低落" in out["persona_text"]
    assert "{{" not in out["persona_text"]


def test_worldbook_recall_triggers(nodes: ChatNodes) -> None:
    out = nodes.worldbook_recall(_state(user_input="我又失眠了"))
    assert "night-mode" in [e.id for e in out["worldbook_hits"]]
    assert out["worldbook_text"]


def test_worldbook_recall_no_hit(nodes: ChatNodes) -> None:
    out = nodes.worldbook_recall(_state(user_input="今天天气不错"))
    assert out["worldbook_hits"] == []
    assert out["worldbook_text"] == ""


def test_memory_recall_empty_initially(nodes: ChatNodes) -> None:
    out = nodes.memory_recall(_state())
    assert out["warm_lines"] == []
    assert out["fact_lines"] == []


def test_assemble_prompt_fixed_order(nodes: ChatNodes) -> None:
    state = {**_state(), "persona_text": "人设", "worldbook_text": "世界书内容",
             "warm_lines": ["回忆"], "fact_lines": ["事实"]}
    out = nodes.assemble_prompt(state)
    prompt = out["system_prompt"]
    assert prompt.index("[角色人设]") < prompt.index("[场景补充]") < prompt.index("[记忆回忆]")
    assert out["messages"][-1]["role"] == "user"


def test_generate_reply_uses_llm(nodes: ChatNodes) -> None:
    out = nodes.generate_reply({**_state(), "messages": [
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "我最近总是失眠"},
    ]})
    assert out["reply"]
    assert "睡" in out["reply"] or "休息" in out["reply"]


def test_write_memory_writes_layers(nodes: ChatNodes, memory: MemoryStore) -> None:
    out = nodes.write_memory({**_state(), "reply": "我听着呢"})
    assert out["writes"]["memory"] == 1      # 情景记忆入库
    assert out["writes"]["facts"] >= 1       # 语义记忆（事实）
    assert memory.cold.list_facts(PERSONA_ID)


# ---------- 图端到端 ----------


def test_graph_node_sequence_defined() -> None:
    """编排顺序：人设 → 世界书 → 记忆 → 组装 → 生成。

    记忆写入**不在图中**：抽取（LLM 版）需 1–3 秒，改由路由层在响应发出后
    后台执行，避免用户等待。
    """
    assert NODE_SEQUENCE == [
        "load_persona",
        "worldbook_recall",
        "knowledge_recall",
        "memory_recall",
        "assemble_prompt",
        "generate_reply",
    ]


def test_graph_end_to_end(graph) -> None:
    result = graph.invoke(_state())

    assert result["reply"]
    assert "[角色人设]" in result["system_prompt"]
    assert result["messages"][-1] == {"role": "user", "content": "我最近总是失眠"}
    # 状态变量进入人设
    assert "小林" in result["persona_text"]
    # 记忆写入已移出图（改为路由层后台执行），因此图输出中不含 writes
    assert "writes" not in result


def test_knowledge_recall_without_retriever_is_empty(nodes: ChatNodes) -> None:
    """未注入检索器时该层为空（功能可关，不阻断对话）。"""
    out = nodes.knowledge_recall(_state())

    assert out["knowledge_lines"] == []


def test_knowledge_recall_populates_lines(memory: MemoryStore) -> None:
    """注入检索器后，用户上传的语料应进入该层。"""
    store = InMemoryKnowledgeStore()
    store.add_document(
        PERSONA_ID, title="咨询室资料", chunks=["咨询室的茶几上常年放着一壶茉莉花茶。"]
    )
    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=memory,
        knowledge=KnowledgeRetriever(store),
        llm_provider=MockLLMProvider(),
    )

    out = nodes.knowledge_recall(_state(user_input="茶几上放着什么"))

    assert out["knowledge_lines"]
    assert "茉莉花茶" in out["knowledge_lines"][0]


def test_knowledge_recall_degrades_on_failure(memory: MemoryStore) -> None:
    """检索异常时记 warning 并降级，不抛异常。"""

    class _BrokenRetriever(KnowledgeRetriever):
        def retrieve(self, companion_id, query, *, top_k=None):  # noqa: ARG002
            raise RuntimeError("模拟知识库不可用")

    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=memory,
        knowledge=_BrokenRetriever(InMemoryKnowledgeStore()),
        llm_provider=MockLLMProvider(),
    )

    out = nodes.knowledge_recall(_state())

    assert out["knowledge_lines"] == []
    assert any("知识库检索失败" in w for w in out["warnings"])


def test_knowledge_lines_reach_system_prompt(memory: MemoryStore) -> None:
    """端到端：参考资料应拼进 system prompt。"""
    store = InMemoryKnowledgeStore()
    store.add_document(PERSONA_ID, title="资料", chunks=["咨询室的茶几上放着茉莉花茶。"])
    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=memory,
        knowledge=KnowledgeRetriever(store),
        llm_provider=MockLLMProvider(),
    )

    recalled = nodes.knowledge_recall(_state(user_input="茶几上放着什么"))
    assembled = nodes.assemble_prompt({**_state(), **recalled})

    assert "[参考资料]" in assembled["system_prompt"]
    assert "茉莉花茶" in assembled["system_prompt"]


def test_graph_worldbook_and_memory_in_prompt(graph) -> None:
    result = graph.invoke(_state(user_input="我又失眠了"))
    prompt = result["system_prompt"]
    assert "night-mode" in [e.id for e in result["worldbook_hits"]]
    assert "[场景补充]" in prompt


def test_graph_memory_cross_turn(graph, nodes: ChatNodes) -> None:
    """第一轮写入的记忆，第二轮应被召回进 prompt（闭环）。

    写入已移出图（改为路由层后台执行），此处显式触发以完成闭环。
    """
    first = graph.invoke(_state(user_input="我最怕打雷，会躲进被子", turn_index=1))
    nodes.write_memory(first)

    second = graph.invoke(_state(user_input="今天又打雷了", turn_index=2))

    assert "[记忆回忆]" in second["system_prompt"]
    assert "打雷" in second["system_prompt"]


def test_graph_history_included_in_messages(graph) -> None:
    from app.session.context import ChatTurn

    history = [
        ChatTurn(role="user", text="昨天没睡好"),
        ChatTurn(role="assistant", text="听起来很辛苦"),
    ]
    result = graph.invoke(_state(history=history, turn_index=3))
    roles = [m["role"] for m in result["messages"]]
    assert roles[0] == "system"
    assert roles[1:-1] == ["user", "assistant"]
    assert roles[-1] == "user"


def test_graph_warnings_accumulate(graph) -> None:
    """缺失状态变量时应有告警，且不中断编排。"""
    result = graph.invoke({**_state(), "state_vars": {}})
    assert result["warnings"]  # current_mood 缺失告警
    assert result["reply"]
