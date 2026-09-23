"""M4 情绪链路集成测试：图节点、情绪加权召回、API 输出。"""

import json

import pytest

from app.graph.chat_graph import build_chat_graph
from app.graph.nodes import ChatNodes
from app.llm.base import AgentResult, ChatMessage, LLMProvider, ToolCall
from app.llm.mock import MockLLMProvider
from app.llm.reasoning import REASONING_MIN_TOKENS
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


# ---------- 模型预设（per-model）----------


def test_assemble_prompt_uses_selected_preset(nodes: ChatNodes) -> None:
    """显式 preset_id 生效，并出现在本轮 sampling 中。"""
    out = nodes.assemble_prompt(_state(preset_id="deepseek"))
    assert out["sampling"].profile_id == "deepseek"
    assert out["sampling"].profile_label == "DeepSeek"


def test_preset_enable_thinking_reaches_provider() -> None:
    """推理开关必须真的传给 provider（否则「按模型分档」只是展示）。"""
    provider = MockLLMProvider()
    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=MemoryStore(SqliteColdStore(db_path=":memory:"), InMemoryWarmStore()),
        llm_provider=provider,
        model_name="deepseek-chat",
    )
    state = _state(user_input="聊聊吧", messages=[{"role": "user", "content": "聊聊吧"}])
    assembled = nodes.assemble_prompt(state)
    assert assembled["sampling"].enable_thinking is False

    nodes.generate_reply({**state, **assembled})
    assert provider.last_enable_thinking is False


def test_graph_emotion_written_to_facts(
    graph, nodes: ChatNodes, memory: MemoryStore
) -> None:
    """本轮情绪应作为 emotion_tag 落到语义记忆（写入已移出图，显式触发）。"""
    result = graph.invoke(_state(user_input="我最近总是失眠，很焦虑"))
    nodes.write_memory(result)

    facts = memory.cold.list_facts(PERSONA_ID)
    assert facts
    assert any(f.emotion_tag == "anxious" for f in facts)


def test_graph_emotion_written_to_warm_metadata(
    graph, nodes: ChatNodes, memory: MemoryStore
) -> None:
    """情景记忆的 metadata 应带情绪标签（供后续加权）。"""
    result = graph.invoke(_state(user_input="我最近总是失眠"))
    nodes.write_memory(result)

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


# ---------- 生成链路的健壮性（实测缺陷的回归用例）----------


def _nodes_with(provider: LLMProvider) -> ChatNodes:
    """用指定 provider 构建节点（其余依赖用内存实现）。"""
    return ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=MemoryStore(SqliteColdStore(db_path=":memory:"), InMemoryWarmStore()),
        llm_provider=provider,
    )


def test_forced_tool_call_rejected_does_not_break_turn() -> None:
    """模型拒绝强制 tool_choice（实测 DeepSeek 系直接 400）→ 不得让整轮对话失败。

    回归背景：降级分支用 tool_choice 强制指定情绪工具，但推理模型的服务端
    会回 `Thinking mode does not support this tool_choice`；旧实现没捕获，
    无预设的对话直接 500，有预设时异常被吞、只剩一个空回复。
    """

    class _RejectingProvider(MockLLMProvider):
        """工具循环给纯文本，但强制 tool_choice 会被服务端拒绝。"""

        def chat_with_tool_loop(self, messages, tools, executor, **kwargs):
            return AgentResult(reply="我在听，你慢慢说。", tool_calls=[], rounds=1)

        def chat_with_tools(self, messages, tools, *, tool_choice="auto", **kwargs):
            raise RuntimeError(
                "Error code: 400 - Thinking mode does not support this tool_choice"
            )

    nodes = _nodes_with(_RejectingProvider())

    out = nodes.generate_reply(
        _state(messages=[{"role": "user", "content": "我最近总是失眠，压力好大"}])
    )

    # 回复没丢（沿用工具循环拿到的文本，不重复生成）
    assert out["reply"] == "我在听，你慢慢说。"
    # 情绪退回关键词兜底（这里命中「失眠/压力」）
    assert out["emotion"].source == "fallback"
    assert any("强制结构化情绪输出失败" in w for w in out["warnings"])


def test_empty_reply_is_explained_in_warnings() -> None:
    """模型确实没产出正文 → warnings 必须说明原因，而不是静默空串。"""

    class _EmptyProvider(MockLLMProvider):
        def chat_with_tool_loop(self, messages, tools, executor, **kwargs):
            return AgentResult(reply="", tool_calls=[], rounds=1)

        def chat(self, messages, **kwargs):
            return ""

        def take_generation_note(self) -> str:
            return "回复为空：max_tokens=350，已被长度上限截断（finish_reason=length）。"

    nodes = _nodes_with(_EmptyProvider())

    out = nodes.generate_reply(_state(messages=[{"role": "user", "content": "你好"}]))

    assert out["reply"] == ""
    assert any("长度上限截断" in w for w in out["warnings"])


def test_empty_reply_is_retried_with_wider_budget() -> None:
    """空回复 + 已知原因（推理模型吃光额度）→ 自动放大额度重试一次。

    回归背景（实测）：新进程首次请求时 provider 还没观测到 reasoning_content，
    max_tokens 仍是 350 → 首轮恒为空。不重试的话用户的第一句话永远是空白。
    """
    budgets: list[int | None] = []

    class _ReasoningFirstProvider(MockLLMProvider):
        """工具循环给空文本；第一次 chat 空、第二次（放大额度后）成功。"""

        model = "deepseek-flash"

        def chat_with_tool_loop(self, messages, tools, executor, **kwargs):
            return AgentResult(reply="", tool_calls=[], rounds=1)

        def chat(self, messages, **kwargs):
            budgets.append(kwargs.get("max_tokens"))
            return "" if len(budgets) == 1 else "我在听，你慢慢说。"

        def take_generation_note(self) -> str:
            return "" if len(budgets) > 1 else "回复为空：max_tokens=350，已被长度上限截断。"

    nodes = _nodes_with(_ReasoningFirstProvider())

    out = nodes.generate_reply(_state(messages=[{"role": "user", "content": "你好"}]))

    assert out["reply"] == "我在听，你慢慢说。"
    assert len(budgets) == 2
    assert budgets[1] >= REASONING_MIN_TOKENS
    # 重试成功后不再报「空回复」告警
    assert not any("长度上限截断" in w for w in out["warnings"])


def test_reasoning_model_gets_max_token_headroom() -> None:
    """推理模型：采样解析要抬高 max_tokens（否则思考吃光额度、正文为空）。

    回归背景（实测）：内置档把 max_tokens 压到 400「防长篇大论」，
    对推理模型等于「只准思考、不准说话」—— deepseek-flash 在 350 时正文恒为空。
    """

    class _ReasoningProvider(MockLLMProvider):
        model = "deepseek-reasoner"

    nodes = _nodes_with(_ReasoningProvider())

    out = nodes.assemble_prompt(_state(persona_text="（测试语料）人设正文", user_input="你好"))

    assert out["sampling"].max_tokens >= REASONING_MIN_TOKENS


def test_observed_reasoning_also_gets_headroom() -> None:
    """模型名不带标记（如 deepseek-flash）但观测到思考 → 同样抬高额度。"""

    class _FlashProvider(MockLLMProvider):
        model = "deepseek-flash"
        saw_reasoning = True

    nodes = _nodes_with(_FlashProvider())

    out = nodes.assemble_prompt(_state(persona_text="（测试语料）人设正文", user_input="你好"))

    assert out["sampling"].max_tokens >= REASONING_MIN_TOKENS


def test_ordinary_model_keeps_profile_max_tokens() -> None:
    """普通模型不得被抬高额度（长度上限是「防长篇大论」的正常取舍）。"""

    class _PlainProvider(MockLLMProvider):
        model = "qwen2.5-7b-instruct"
        saw_reasoning = False

    nodes = _nodes_with(_PlainProvider())

    out = nodes.assemble_prompt(_state(persona_text="（测试语料）人设正文", user_input="你好"))

    assert out["sampling"].max_tokens < REASONING_MIN_TOKENS
