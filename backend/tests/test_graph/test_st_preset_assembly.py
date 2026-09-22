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
from app.prompts.style.models import StylePreset


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


def test_worldbook_goes_to_before_slot_by_default() -> None:
    """默认注入 worldInfoBefore（ST 默认顺序里它承载情境补充）。"""
    st = _preset_with_markers(enabled=["worldInfoBefore", "worldInfoAfter"])

    before, after, warnings = ChatNodes._split_worldbook({"worldbook_text": "命中内容"}, st)

    assert before == "命中内容"
    assert after == ""
    assert warnings == []


def test_worldbook_falls_back_to_after_slot() -> None:
    """worldInfoBefore 未启用时改投 worldInfoAfter。"""
    st = _preset_with_markers(enabled=["worldInfoAfter"])

    before, after, _ = ChatNodes._split_worldbook({"worldbook_text": "命中内容"}, st)

    assert before == ""
    assert after == "命中内容"


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
