"""ST 预设组装在 graph 节点层的单元测试（采样合并 / 槽位选择 / 记忆拼装）。

夹具全部自写（结构与酒馆预设对齐、正文为测试语料），见 docs/st-preset-compat.md §1。
"""

import pytest

from app.graph.nodes import ChatNodes
from app.llm.mock import MockLLMProvider
from app.llm.profiles import load_model_profiles
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.prompts.st_compat import parse_st_preset
from app.prompts.assemble import DepthInjection
from app.prompts.style.models import StylePreset
from app.rag.prompt_manager import DIALOGUE_ONLY_RULE
from app.session.context import ChatTurn


@pytest.fixture
def nodes(tmp_path) -> ChatNodes:
    """最小可用的节点实例（只用到采样合并与静态工具方法）。"""
    return ChatNodes(
        presets={},
        entries=[],
        memory_store=MemoryStore(
            SqliteColdStore(db_path=tmp_path / "memory.db"), InMemoryWarmStore()
        ),
        llm_provider=MockLLMProvider(),
        model_name="qwen2.5-7b-instruct",
        profiles=load_model_profiles(),
    )


def make_st_preset(**overrides) -> dict:
    """自造脱敏预设（默认只含采样参数，便于单独验证合并规则）。"""
    preset = {"temperature": 0.66, "top_p": 0.9, "prompts": [], "prompt_order": []}
    preset.update(overrides)
    return preset


def make_style(**overrides) -> StylePreset:
    values = {
        "id": "test-style",
        "name": "测试文风",
        "description": "测试用",
        "style_prompt": "（测试语料）简短一点。",
        "sampling": {},
    }
    values.update(overrides)
    return StylePreset(**values)


# --------------------------------------------------------------------------
# 采样三级合并（内置档 → ST 预设 → 文风预设）
# --------------------------------------------------------------------------


def test_st_preset_overrides_model_profile(nodes) -> None:
    """ST 预设覆盖内置档，并记录来源。"""
    st = parse_st_preset(make_st_preset())

    sampling = nodes._resolve_sampling_with_st(
        {"st_preset_id": "tavern", "preset_id": ""}, None, st
    )

    assert sampling.temperature == 0.66
    assert sampling.sources["temperature"] == "st:tavern"


def test_style_preset_has_highest_priority(nodes) -> None:
    """文风预设覆盖 ST 预设（三级合并的最终优先者）。"""
    st = parse_st_preset(make_st_preset())
    style = make_style(sampling={"temperature": 0.5})

    sampling = nodes._resolve_sampling_with_st(
        {"st_preset_id": "tavern", "preset_id": ""}, style, st
    )

    assert sampling.temperature == 0.5
    assert sampling.sources["temperature"] == "style"
    assert sampling.sources["top_p"] == "st:tavern"     # 未被文风覆盖的项仍来自 ST


def test_show_thoughts_maps_to_enable_thinking(nodes) -> None:
    """ST 的 show_thoughts 与本项目 enable_thinking 同向映射。"""
    st = parse_st_preset({"show_thoughts": True, "prompts": []})
    absent = parse_st_preset({"prompts": []})

    assert st.preset.enable_thinking is True
    assert absent.preset.enable_thinking is None

    sampling = nodes._resolve_sampling_with_st({"st_preset_id": "x"}, None, st)
    assert sampling.enable_thinking is True


# --------------------------------------------------------------------------
# 世界书槽位选择
# --------------------------------------------------------------------------


def _preset_with_markers(*, enabled: list[str]) -> object:
    return parse_st_preset(
        {
            "prompts": [{"identifier": i, "name": i, "marker": True} for i in enabled],
            "prompt_order": [
                {"character_id": 1, "order": [{"identifier": i, "enabled": True} for i in enabled]}
            ],
        }
    )


def test_each_position_maps_to_its_own_st_slot() -> None:
    """★ 两个档位各投各的 ST 槽位（与 ST 自己的语义一致）。

    `before_char`（人设前的设定）→ `worldInfoBefore`（ST 里它紧贴角色定义之前）；
    `after_char`（人设后，也是本项目内置 / 自建条目的默认档）→ `worldInfoAfter`。

    注意这是一次**可见的行为变化**：从前只有一块世界书，所有命中都优先投
    `worldInfoBefore`；现在各归其位。两个槽位在 ST 默认顺序里紧贴角色定义，
    属位置微调，不是内容丢失。
    """
    st = _preset_with_markers(enabled=["worldInfoBefore", "worldInfoAfter"])

    before, after, warnings = ChatNodes._split_worldbook(
        {"worldbook_before_text": "人设前", "worldbook_text": "人设后"}, st
    )

    assert before == "人设前"
    assert after == "人设后"
    assert warnings == []


def test_missing_slot_gets_the_other_group_merged_in() -> None:
    """槽位没启用时合并投给另一个：宁可就错位置，也不能静默丢掉命中。"""
    st = _preset_with_markers(enabled=["worldInfoAfter"])

    before, after, _ = ChatNodes._split_worldbook(
        {"worldbook_before_text": "人设前", "worldbook_text": "人设后"}, st
    )

    assert before == ""
    assert after == "人设前\n\n人设后"


def test_before_slot_only_gets_everything() -> None:
    st = _preset_with_markers(enabled=["worldInfoBefore"])

    before, after, _ = ChatNodes._split_worldbook(
        {"worldbook_before_text": "人设前", "worldbook_text": "人设后"}, st
    )

    assert before == "人设前\n\n人设后"
    assert after == ""


def test_worldbook_warns_when_no_slot_enabled() -> None:
    """两个槽位都没启用时告警，而不是静默丢掉世界书命中。"""
    st = _preset_with_markers(enabled=["main"])

    before, after, warnings = ChatNodes._split_worldbook({"worldbook_text": "命中内容"}, st)

    assert (before, after) == ("", "")
    assert any("世界书" in warning for warning in warnings)


def test_worldbook_empty_text_produces_nothing() -> None:
    st = _preset_with_markers(enabled=["worldInfoBefore"])

    assert ChatNodes._split_worldbook({"worldbook_text": "  "}, st) == ("", "", [])


# --------------------------------------------------------------------------
# at_depth 档：ST 路径与内置路径用同一套深度插入
# --------------------------------------------------------------------------


def test_at_depth_worldbook_is_inserted_into_st_messages(nodes) -> None:
    """★ ST 预设路径也要按 depth 插入，与内置路径行为一致。

    两条路径的 depth 语义不一致的话，同一份世界书在「桌宠模式」与「酒馆模式」
    会表现出两种行为——那是最难排查的一类 bug。
    """
    st = _preset_with_markers(enabled=["main", "chatHistory"])
    state = {
        "persona_id": "demo",
        "user_input": "这一轮问",
        "history": [
            ChatTurn(role="user", text="上一轮问"),
            ChatTurn(role="assistant", text="上一轮答"),
        ],
        "worldbook_depth": [DepthInjection(depth=1, text="靠近输入的设定")],
        "st_preset": st,
    }

    out = nodes.assemble_prompt(state)

    contents = [message["content"] for message in out["messages"]]
    # 末尾会多一条「输出形态」system 块（该预设没启用 jailbreak 槽位 → 追加到末尾，
    # 见 `_assemble_with_st_preset`），因此先确认它，再从后往前断言深度插入的位置
    assert DIALOGUE_ONLY_RULE in contents[-1]
    assert contents[-2] == "这一轮问"
    assert contents[-3] == "靠近输入的设定"


def test_no_at_depth_leaves_st_messages_untouched(nodes) -> None:
    """没有 at_depth 档时对话消息列表与从前完全一致。"""
    st = _preset_with_markers(enabled=["main", "chatHistory"])
    state = {
        "persona_id": "demo",
        "user_input": "这一轮问",
        "history": [ChatTurn(role="user", text="上一轮问")],
        "st_preset": st,
    }

    out = nodes.assemble_prompt(state)

    # 只看对话消息：末尾多出来的那条是产品级输出形态约束，不属于对话内容
    dialogue = [
        message["content"]
        for message in out["messages"]
        if message["role"] in ("user", "assistant")
    ]
    assert dialogue == ["上一轮问", "这一轮问"]


def test_output_form_rule_always_injected_in_st_path(nodes) -> None:
    """★ 酒馆路径同样「只说话」（全局强制）：没选文风也必须带上形态约束。

    这条路径不经过 `PromptManager` 的 `LAYER_FORMAT`，约束搭在文风块上。
    如果只在选了文风时才注入，用户把文风设成 `none` 就能静默绕过「不写旁白」——
    那会让两个模式的行为不一致，是最难排查的一类差异。
    """
    st = _preset_with_markers(enabled=["main", "chatHistory"])
    state = {
        "persona_id": "demo",
        "user_input": "这一轮问",
        "style_id": "none",
        "st_preset": st,
    }

    out = nodes.assemble_prompt(state)

    assert any(
        DIALOGUE_ONLY_RULE in message["content"] for message in out["messages"]
    )


# --------------------------------------------------------------------------
# 记忆块拼装与其它工具
# --------------------------------------------------------------------------


def test_memory_text_sections_match_builtin_path() -> None:
    """记忆块分节与内置 PromptManager 口径一致（[参考资料] / [记忆回忆]）。"""
    text = ChatNodes._compose_memory_text(
        {
            "knowledge_lines": ["资料A"],
            "warm_lines": ["回忆B"],
            "fact_lines": ["事实C"],
        }
    )

    assert "[参考资料]" in text
    assert "- 资料A" in text
    assert "[记忆回忆]" in text
    assert "相关回忆：" in text and "- 回忆B" in text
    assert "已知事实：" in text and "- 事实C" in text


def test_memory_text_skips_empty_layers() -> None:
    """没有任何层时不产生空块。"""
    assert ChatNodes._compose_memory_text({}) == ""
    assert ChatNodes._compose_memory_text({"warm_lines": ["", "  "]}) == ""


def test_persona_personality_uses_title_and_tags() -> None:
    """人设「性格」段由定位 + 标签近似（本项目 persona 无独立字段）。"""
    class _Persona:
        title = "温柔倾听者"
        tags = ["温柔", "共情"]

    assert ChatNodes._persona_personality(_Persona()) == "温柔倾听者；温柔、共情"
    assert ChatNodes._persona_personality(None) == ""
