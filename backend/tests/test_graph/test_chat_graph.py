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
from app.memory.knowledge.scopes import book_scope
from app.memory.store import MemoryStore
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.prompts.assemble import DepthInjection
from app.prompts.persona.loader import load_builtin_presets
from app.rag.prompt_manager import PromptManager
from app.session.mode import MODE_COMPANION, MODE_TAVERN
from app.worldbook.loader import load_builtin_entries
from app.worldbook.models import WorldBookEntry

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


def test_load_persona_fills_char_name_from_the_persona(nodes: ChatNodes) -> None:
    """`{{char_name}}` 取**本轮人设**的角色名，不用注册表的兜底值。

    酒馆角色卡把这条链看重到不行：正文里的 `{{char}}` 会被映射层改写成
    `{{char_name}}`，拿兜底值渲染就会把角色在提示词里叫成「角色」。
    """
    persona = nodes.presets[PERSONA_ID]
    nodes.presets[PERSONA_ID] = persona.model_copy(
        update={"prompt": "你是{{char_name}}，{{user_name}} 的朋友。", "name": "苏澄"}
    )

    out = nodes.load_persona(_state())

    assert "你是苏澄" in out["persona_text"]
    assert out["warnings"] == []  # 已提供，不该再报「用默认值」


def test_state_var_char_name_wins_over_persona_default(nodes: ChatNodes) -> None:
    """运行时显式传入的 `char_name` 优先（与 `user_name` 同一套优先级）。"""
    persona = nodes.presets[PERSONA_ID]
    nodes.presets[PERSONA_ID] = persona.model_copy(update={"prompt": "我是{{char_name}}。"})

    out = nodes.load_persona(_state(state_vars={"char_name": "临时改名"}))

    assert "我是临时改名。" in out["persona_text"]


def test_load_persona_appends_background_after_prompt(nodes: ChatNodes) -> None:
    """角色层 = 人设正文 → [背景故事]，顺序固定（背景排在正文之后）。"""
    persona = nodes.presets[PERSONA_ID]
    body = "苏澄在海边长大。"
    nodes.presets[PERSONA_ID] = persona.model_copy(update={"background": body})

    out = nodes.load_persona(_state())

    text = out["persona_text"]
    assert text.index(body) > text.index(persona.prompt[:12])
    assert "[背景故事]" in text


def test_load_persona_without_background_has_no_empty_block(nodes: ChatNodes) -> None:
    """没有背景故事时不该凭空多出一个空块（内置角色默认如此）。"""
    out = nodes.load_persona(_state())
    assert "[背景故事]" not in out["persona_text"]


def test_load_persona_unknown_persona_warns_instead_of_crashing(nodes: ChatNodes) -> None:
    """预设快照里没有的角色 → 告警且不注入人设，而不是 KeyError 打掉整轮。"""
    out = nodes.load_persona(_state(persona_id="user-just-deleted"))
    assert out["persona_text"] == ""
    assert any("user-just-deleted" in warning for warning in out["warnings"])


def test_worldbook_recall_filters_scoped_entries_by_companion(nodes: ChatNodes) -> None:
    """专属条目只在其归属角色的轮次里命中（节点层把 companion_id 传下去）。"""
    nodes.entries = [
        *nodes.entries,
        WorldBookEntry(
            id="user-secret",
            title="专属设定",
            content="只有她知道的事",
            keys=["秘密"],
            scope="user-other",
        ),
    ]

    mine = nodes.worldbook_recall(_state(user_input="我有个秘密"))
    assert [entry.id for entry in mine["worldbook_hits"]] == []

    theirs = nodes.worldbook_recall(
        _state(
            user_input="我有个秘密",
            persona_id="user-other",
            companion_id="user-other",
        )
    )
    assert [entry.id for entry in theirs["worldbook_hits"]] == ["user-secret"]


def test_worldbook_recall_triggers(nodes: ChatNodes) -> None:
    out = nodes.worldbook_recall(_state(user_input="我又失眠了"))
    assert "night-mode" in [e.id for e in out["worldbook_hits"]]
    assert out["worldbook_text"]


def test_worldbook_recall_no_hit(nodes: ChatNodes) -> None:
    out = nodes.worldbook_recall(_state(user_input="今天天气不错"))
    assert out["worldbook_hits"] == []
    assert out["worldbook_text"] == ""


# ---------- 世界书分档（AGENTS.md §8.4）----------


def _worldbook_nodes(memory: MemoryStore, *entries: WorldBookEntry) -> ChatNodes:
    """只装给定世界书条目的节点（内置条目会干扰「命中哪几条」的断言）。"""
    return ChatNodes(
        presets=load_builtin_presets(),
        entries=list(entries),
        memory_store=memory,
        llm_provider=MockLLMProvider(),
        prompt_manager=PromptManager(),
    )


def _placed(**overrides) -> WorldBookEntry:
    base = {
        "id": "demo",
        "title": "演示",
        "content": "演示正文",
        "keys": ["触发"],
        "position": "after_char",
    }
    base.update(overrides)
    return WorldBookEntry.model_validate(base)


def test_worldbook_recall_splits_by_position(memory: MemoryStore) -> None:
    """★ 三种档位各归各的 state 键：人设前 / 人设后 / 深度注入。"""
    nodes = _worldbook_nodes(
        memory,
        _placed(id="before", title="前", content="人设前的设定", position="before_char"),
        _placed(id="after", title="后", content="人设后的补充"),
        _placed(
            id="deep",
            title="深",
            content="靠近输入",
            position="at_depth",
            depth=1,
            role="user",
        ),
    )

    out = nodes.worldbook_recall(_state(user_input="触发一下"))

    assert out["worldbook_before_text"] == "[前]\n人设前的设定"
    assert out["worldbook_text"] == "[后]\n人设后的补充"
    assert [(i.depth, i.role, i.text) for i in out["worldbook_depth"]] == [
        (1, "user", "[深]\n靠近输入")
    ]


def test_assemble_prompt_places_before_block_ahead_of_persona(memory: MemoryStore) -> None:
    """★ 端到端：`[背景设定]` 在 `[角色人设]` 之前，at_depth 进消息而非 system。"""
    nodes = _worldbook_nodes(memory)
    state = {
        **_state(),
        "persona_text": "人设正文",
        "worldbook_before_text": "人设前的设定",
        "worldbook_text": "人设后的补充",
        "worldbook_depth": [DepthInjection(depth=1, text="靠近输入的设定")],
    }

    out = nodes.assemble_prompt(state)

    prompt = out["system_prompt"]
    assert prompt.index("[背景设定]") < prompt.index("[角色人设]")
    assert prompt.index("[角色人设]") < prompt.index("[场景补充]")
    # 深度注入在**消息**里，紧贴在用户输入之前
    assert "靠近输入的设定" not in prompt
    assert out["messages"][-2]["content"] == "靠近输入的设定"
    assert out["messages"][-1]["content"] == state["user_input"]


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


# ---------- 耗时埋点（排查「回复慢」的观测面）----------


def test_graph_records_per_node_timings(graph) -> None:
    """★ 每个节点都要留下耗时，否则「慢在哪」只能靠猜。"""
    result = graph.invoke(_state())

    timings = result["timings"]
    assert set(NODE_SEQUENCE) <= set(timings)
    assert all(value >= 0 for value in timings.values())


def test_generate_reply_reports_llm_rounds(nodes: ChatNodes) -> None:
    """LLM 调用次数必须能被看到——它是「回复为什么慢」最直接的解释量。"""
    out = nodes.generate_reply(
        {
            **_state(),
            "messages": [{"role": "user", "content": "我最近总是失眠"}],
        }
    )

    assert out["llm_rounds"] >= 1


def test_emotion_probe_omits_system_prompt_and_caps_tokens() -> None:
    """★ 强制补调情绪只发极简消息、且压小额度。

    原实现会把整份 system prompt（人设 + 世界书 + 记忆 + 技能清单，数千 token）
    重发一遍，并要求模型再写一遍完整回复——而那份回复随后会被已生成正文覆盖，
    等于白付一次完整的 prefill + decode。
    """
    from app.graph.nodes import (
        _EMOTION_PROBE_MAX_TOKENS,
        _emotion_probe_kwargs,
        _emotion_probe_messages,
    )

    state = {
        **_state(),
        "messages": [
            {"role": "system", "content": "很长的人设与记忆" * 200},
            {"role": "user", "content": "我最近总是失眠"},
        ],
    }
    messages = _emotion_probe_messages(state, "我在这儿，慢慢说。")

    assert len(messages) == 1
    assert messages[0].role == "user"
    assert all(m.role != "system" for m in messages)
    assert "很长的人设与记忆" not in messages[0].content
    assert "我最近总是失眠" in messages[0].content

    capped = _emotion_probe_kwargs({"max_tokens": 3000, "temperature": 0.8})
    assert capped["max_tokens"] == _EMOTION_PROBE_MAX_TOKENS
    assert capped["temperature"] == 0.8  # 采样参数沿用本轮

    # 开了思考的模型不动额度：思考 token 会先吃掉预算，压小会让 JSON 出不来
    thinking = {"max_tokens": 3000, "enable_thinking": True}
    assert _emotion_probe_kwargs(thinking)["max_tokens"] == 3000


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


# =============================================================
# 知识召回：酒馆来源只在酒馆聊天模式可见（AGENTS.md §8.2）
# =============================================================


def _nodes_with_knowledge(memory: MemoryStore, store) -> ChatNodes:
    return ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=memory,
        knowledge=KnowledgeRetriever(store, top_k=3, candidate_n=10),
        llm_provider=MockLLMProvider(),
        prompt_manager=PromptManager(),
    )


def _tavern_store() -> InMemoryKnowledgeStore:
    """一本书的库：内容写进**这本书自己**的作用域（`tavern:book:{哈希}`）。

    旧版写在一个共享的 `tavern` 作用域里，因此「挂 A 书」与「挂 B 书」没有区别；
    改成单本作用域后，**挂哪本才查得到哪本**——下面两条用例分别验证这两面。
    """
    store = InMemoryKnowledgeStore()
    store.add_document(
        book_scope("world/示例世界"),
        title="酒馆世界书",
        chunks=["某作品的设定：她住在城南。"],
    )
    return store


@pytest.fixture
def mounted_example_book(monkeypatch):
    """把「示例世界」设为已挂载（挂载清单默认空，不挂就查不到）。"""
    monkeypatch.setattr(
        "app.graph.nodes.load_mounted_books", lambda: ["world/示例世界"]
    )


def test_knowledge_recall_hides_tavern_worldbook_in_companion_mode(
    memory, mounted_example_book
) -> None:
    """★ 桌宠模式查不到酒馆世界书——内置人设不该「知道」别的作品的设定。

    即便用户挂上了这本书：挂载清单**只对酒馆模式生效**（`AGENTS.md §8.2`）。
    """
    nodes = _nodes_with_knowledge(memory, _tavern_store())

    result = nodes.knowledge_recall(_state(mode=MODE_COMPANION))

    assert result["knowledge_lines"] == []


def test_knowledge_recall_sees_tavern_worldbook_in_tavern_mode(
    memory, mounted_example_book
) -> None:
    """★ 酒馆模式 + 已挂载 → 查得到——否则这条链路就是白同步。"""
    nodes = _nodes_with_knowledge(memory, _tavern_store())

    result = nodes.knowledge_recall(
        _state(mode=MODE_TAVERN, user_input="她住在城南还是城北")
    )

    assert result["knowledge_lines"]
    assert "城南" in result["knowledge_lines"][0]


def test_knowledge_recall_ignores_unmounted_book(memory) -> None:
    """★ 没挂的书查不到（默认全不挂）：库里有数据 ≠ 会被召回。

    这是本次改动的核心语义——「哪个世界书就是在哪个世界书里，
    切换到这个世界书才有对应的知识」。
    """
    nodes = _nodes_with_knowledge(memory, _tavern_store())

    result = nodes.knowledge_recall(
        _state(mode=MODE_TAVERN, user_input="她住在城南还是城北")
    )

    assert result["knowledge_lines"] == []


def test_knowledge_recall_defaults_to_companion_mode(memory, mounted_example_book) -> None:
    """state 里没带 `mode`（老调用方 / 手搓 state）时按桌宠处理：宁可少注入。

    这是刻意的 fail-safe：酒馆内容漏进桌宠会话，比桌宠少召回一条知识更糟。
    """
    nodes = _nodes_with_knowledge(memory, _tavern_store())

    assert nodes.knowledge_recall(_state())["knowledge_lines"] == []
