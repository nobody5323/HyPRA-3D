"""风格系统集成测试：PromptManager 风格层 + few-shot 示例 + 图/API 接入。"""

import pytest
from fastapi.testclient import TestClient

from app.graph.chat_graph import build_chat_graph
from app.graph.nodes import ChatNodes
from app.llm.mock import MockLLMProvider
from app.llm.profiles import ResolvedSampling
from app.main import app
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.prompts.persona.loader import load_builtin_presets
from app.prompts.style.loader import load_builtin_styles
from app.rag.prompt_manager import LAYER_STYLE, PromptManager
from app.session.context import ChatTurn
from app.worldbook.loader import load_builtin_entries

PERSONA_ID = "therapist-elder-sister"
client = TestClient(app)


# ---------- PromptManager 层 ----------


def test_style_layer_at_end_of_system_prompt() -> None:
    """风格块排在 system 末尾（越靠近输入影响越强），其后只跟「回复格式」约束。"""
    pm = PromptManager()
    result = pm.build(
        persona_text="人设",
        user_input="问",
        worldbook_text="世界书",
        warm_lines=["回忆"],
        style_text="【表达风格：现代口语】说人话。",
    )
    prompt = result.system_prompt
    assert prompt.index("[角色人设]") < prompt.index("世界书") < prompt.index("【表达风格")
    # 内容层（人设 / 世界书 / 记忆）之后才是风格块；输出约束排在它之后收尾
    assert prompt.index("【表达风格") < prompt.index("【回复格式】")
    assert prompt.rstrip().endswith("等对方回应再接着说。")


def test_style_layer_recorded_in_layers() -> None:
    pm = PromptManager()
    result = pm.build(persona_text="人设", user_input="问", style_text="短句为主。")
    assert LAYER_STYLE in result.layers
    assert result.layers[LAYER_STYLE].tokens > 0


def test_examples_injected_before_history() -> None:
    """示例对话（few-shot）应插在 system 之后、历史之前。"""
    pm = PromptManager()
    history = [ChatTurn(role="user", text="昨天的事")]
    result = pm.build(
        persona_text="人设",
        user_input="今天呢",
        history=history,
        style_text="风格",
        examples=[("示例问", "示例答")],
    )
    msgs = result.messages
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "示例问"}
    assert msgs[2] == {"role": "assistant", "content": "示例答"}
    assert msgs[3] == {"role": "user", "content": "昨天的事"}   # 真实历史在后
    assert msgs[-1] == {"role": "user", "content": "今天呢"}


def test_example_count_reported() -> None:
    pm = PromptManager()
    result = pm.build(
        persona_text="人设", user_input="问", examples=[("a", "b"), ("c", "d")]
    )
    assert result.example_count == 2


def test_examples_respect_budget() -> None:
    """示例对话超额时应截断（不挤爆上下文）。"""
    pm = PromptManager(style_budget=60)
    result = pm.build(
        persona_text="人设",
        user_input="问",
        style_text="风格",
        examples=[("很长的示例问题" * 20, "很长的示例回答" * 20)] * 3,
    )
    assert result.example_count < 3
    assert any("示例对话超出预算" in w for w in result.warnings)


def test_style_dropped_under_total_budget_pressure() -> None:
    """总量预算极紧时，风格层可被裁减（人设必留）。"""
    pm = PromptManager(total_budget=30, style_budget=500)
    result = pm.build(
        persona_text="人设在",
        user_input="问",
        style_text="很长的风格说明" * 50,
    )
    assert LAYER_STYLE in result.dropped_layers
    assert result.layers["persona"].body == "人设在"


# ---------- 图集成 ----------


@pytest.fixture()
def nodes(tmp_path) -> ChatNodes:
    return ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=MemoryStore(SqliteColdStore(db_path=tmp_path / "m.db"), InMemoryWarmStore()),
        llm_provider=MockLLMProvider(),
        styles=load_builtin_styles(),
        default_style_id="modern-conversational",
        model_name="qwen3.7-flash-2026-07-15",
    )


def _state(**overrides) -> dict:
    base = {
        "session_id": "s1", "companion_id": PERSONA_ID, "persona_id": PERSONA_ID,
        "user_name": "小林", "user_input": "我最近总是失眠", "history": [],
        "turn_index": 1, "state_vars": {}, "style_id": "", "warnings": [],
    }
    base.update(overrides)
    return base


def test_assemble_prompt_adds_style_and_sampling(nodes: ChatNodes) -> None:
    state = {**_state(), "persona_text": "人设", "user_input": "问"}
    out = nodes.assemble_prompt(state)

    assert out["style_id"] == "modern-conversational"
    assert isinstance(out["sampling"], ResolvedSampling)
    assert out["example_count"] >= 2
    assert "【表达风格：现代口语】" in out["system_prompt"]


def test_assemble_prompt_style_override(nodes: ChatNodes) -> None:
    state = {**_state(style_id="brief-direct"), "persona_text": "人设", "user_input": "问"}
    out = nodes.assemble_prompt(state)
    assert out["style_id"] == "brief-direct"
    assert out["sampling"].max_tokens == 140   # 简短利落的采样建议生效


def test_assemble_prompt_unknown_style_warns(nodes: ChatNodes) -> None:
    state = {**_state(style_id="no-such-style"), "persona_text": "人设", "user_input": "问"}
    out = nodes.assemble_prompt(state)
    assert out["style_id"] == ""
    assert any("未知风格预设" in w for w in out["warnings"])


def test_graph_uses_style_sampling(nodes: ChatNodes) -> None:
    """端到端：风格生效且采样参数来自模型档 ⊕ 文风。"""
    graph = build_chat_graph(nodes)
    result = graph.invoke(_state())
    assert result["style_id"] == "modern-conversational"
    sampling = result["sampling"]
    assert sampling is not None
    # 文风覆盖 max_tokens，模型档保留 top_p
    assert sampling.max_tokens == 220
    assert sampling.top_p == 0.9
    # 示例对话进入 messages
    roles = [m["role"] for m in result["messages"]]
    assert roles.count("assistant") >= 2


def test_graph_without_styles_still_works(tmp_path) -> None:
    """未注入风格预设时（老配置）不应报错：风格层为空。"""
    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=MemoryStore(SqliteColdStore(db_path=tmp_path / "m.db"), InMemoryWarmStore()),
        llm_provider=MockLLMProvider(),
    )
    result = build_chat_graph(nodes).invoke(_state())
    assert result["style_id"] == ""
    assert result["reply"]


# ---------- API ----------


def test_chat_returns_style_meta() -> None:
    resp = client.post("/chat", json={"text": "我最近总是失眠"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["style"]["style_id"]
    assert body["style"]["style_name"]
    assert body["style"]["examples"] >= 1
    assert "temperature" in body["style"]["sampling"]


def test_chat_style_switch() -> None:
    """A/B 开关：请求指定不同风格应生效。"""
    a = client.post("/chat", json={"text": "我好累", "style_id": "brief-direct"}).json()
    b = client.post("/chat", json={"text": "我好累", "style_id": "gentle-elaborate"}).json()
    assert a["style"]["style_id"] == "brief-direct"
    assert b["style"]["style_id"] == "gentle-elaborate"
    assert a["style"]["sampling"]["max_tokens"] < b["style"]["sampling"]["max_tokens"]
    # 风格指令进入 system
    assert "简短利落" in a["system_prompt"]
    assert "细腻长句" in b["system_prompt"]
