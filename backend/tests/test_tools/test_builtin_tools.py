"""内置工具测试：情绪日记 / 趋势 / 呼吸引导 / 主动记忆检索。"""

import json

import pytest

from app.memory.cold.mood_log import MoodLogEntry, SqliteMoodLogStore
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.tools.builtin_tools import (
    BREATHING_PATTERNS,
    build_default_registry,
    build_tool_context,
    normalize_emotion,
)
from app.tools.registry import ToolRegistry


@pytest.fixture()
def mood_store(tmp_path) -> SqliteMoodLogStore:
    return SqliteMoodLogStore(db_path=tmp_path / "mood.db")


@pytest.fixture()
def memory_store(tmp_path) -> MemoryStore:
    return MemoryStore(SqliteColdStore(db_path=tmp_path / "memory.db"), InMemoryWarmStore())


@pytest.fixture()
def registry(tmp_path) -> ToolRegistry:
    """内置工具集，且**技能库置空**。

    不置空的话结果会随 `backend/skills/` 的内容而变（有技能时会多一个
    `study_skill`）——测试不该依赖仓库里恰好放了几个技能文件。
    技能相关的行为由下面专门的用例覆盖。
    """
    from app.skills.registry import SkillRegistry

    return build_default_registry(skill_registry=SkillRegistry(state_path=tmp_path / "skills.json"))


def _ctx(mood_store=None, memory_store=None):
    return build_tool_context(
        "therapist-elder-sister",
        session_id="s1",
        user_name="小林",
        mood_store=mood_store,
        memory_store=memory_store,
    )


# ---------- 情绪归一化 ----------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("anxious", "anxious"),
        ("ANXIOUS", "anxious"),
        ("焦虑", "anxious"),
        ("难过", "sad"),
        ("", "neutral"),
        ("未知情绪", "neutral"),
    ],
)
def test_normalize_emotion(value: str, expected: str) -> None:
    assert normalize_emotion(value) == expected


# ---------- 注册表内容 ----------


def test_default_registry_has_four_tools(registry: ToolRegistry) -> None:
    assert set(registry.names()) == {
        "record_mood_journal",
        "query_mood_trend",
        "start_breathing_exercise",
        "recall_memory",
    }


def test_study_skill_registered_only_when_skills_exist(tmp_path) -> None:
    """`study_skill` 只在存在启用技能时注册。

    空技能库还挂着它，模型就多了一个永远失败的选项，白占 tools schema 的 token。
    """
    from app.skills.registry import SkillRegistry

    empty = SkillRegistry(state_path=tmp_path / "skills.json")
    assert "study_skill" not in build_default_registry(skill_registry=empty).names()

    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: 演示技能\ndescription: 一句话说明\n---\n\n正文步骤。\n",
        encoding="utf-8",
    )
    loaded = SkillRegistry(state_path=tmp_path / "skills.json")
    loaded.load(builtin_dir=tmp_path / "skills")
    assert "study_skill" in build_default_registry(skill_registry=loaded).names()


def test_all_tools_export_valid_schema(registry: ToolRegistry) -> None:
    for schema in registry.schemas():
        fn = schema["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"


# ---------- 工具 1：记录情绪日记 ----------


def test_record_mood_writes_entry(registry: ToolRegistry, mood_store) -> None:
    result = registry.execute(
        "record_mood_journal",
        json.dumps({"emotion": "焦虑", "intensity": 0.8, "trigger": "工作汇报"}),
        _ctx(mood_store=mood_store),
    )
    assert result.success is True
    assert "已帮你记下" in result.content
    assert result.data["emotion"] == "anxious"          # 中文已归一化

    entries = mood_store.list_recent("therapist-elder-sister")
    assert len(entries) == 1
    assert entries[0].emotion == "anxious"
    assert entries[0].trigger == "工作汇报"
    assert entries[0].session_id == "s1"


def test_record_mood_without_store_degrades(registry: ToolRegistry) -> None:
    """未注入日记库时友好降级，不报错。"""
    result = registry.execute("record_mood_journal", {"emotion": "sad"}, _ctx())
    assert result.success is True
    assert "不可用" in result.content


# ---------- 工具 2：情绪趋势 ----------


def test_query_trend_empty(registry: ToolRegistry, mood_store) -> None:
    result = registry.execute(
        "query_mood_trend", json.dumps({"days": 7}), _ctx(mood_store=mood_store)
    )
    assert result.success is True
    assert "还没有记录" in result.content


def test_query_trend_summarizes(registry: ToolRegistry, mood_store) -> None:
    for emotion, intensity in [("anxious", 0.8), ("anxious", 0.7), ("happy", 0.4)]:
        mood_store.add(
            MoodLogEntry(
                companion_id="therapist-elder-sister", emotion=emotion, intensity=intensity
            )
        )
    result = registry.execute(
        "query_mood_trend", json.dumps({"days": 7}), _ctx(mood_store=mood_store)
    )
    assert result.success is True
    assert "3 次" in result.content
    assert "焦虑" in result.content          # 主导情绪已中文化
    assert result.data["trend"]["dominant"] == "anxious"


# ---------- 工具 3：呼吸引导 ----------


def test_breathing_478(registry: ToolRegistry) -> None:
    result = registry.execute(
        "start_breathing_exercise",
        json.dumps({"pattern": "478", "cycles": 3}),
        _ctx(),
    )
    assert result.success is True
    assert "4-7-8 呼吸法" in result.content
    assert result.data["cycle_seconds"] == 19      # 4+7+8
    assert result.data["total_seconds"] == 57      # 19×3
    assert result.data["ssml"].startswith("<speak>")
    assert len(result.data["phases"]) == 3


def test_breathing_box_pattern(registry: ToolRegistry) -> None:
    result = registry.execute(
        "start_breathing_exercise", json.dumps({"pattern": "box"}), _ctx()
    )
    assert result.data["pattern_name"] == "方块呼吸"
    assert result.data["cycle_seconds"] == 16      # 4×4


def test_breathing_unknown_pattern_falls_back(registry: ToolRegistry) -> None:
    result = registry.execute(
        "start_breathing_exercise", json.dumps({"pattern": "weird"}), _ctx()
    )
    assert result.success is True
    assert result.data["pattern_name"] == BREATHING_PATTERNS["478"]["name"]


def test_breathing_ssml_has_no_ka_by_default(registry: ToolRegistry) -> None:
    """平静强度下的引导不带 KA 动作（避免动作泛滥）。"""
    result = registry.execute(
        "start_breathing_exercise", json.dumps({"pattern": "478"}), _ctx()
    )
    assert result.data["display_text"]
    assert "<" not in result.data["display_text"]


# ---------- 工具 4：主动记忆检索 ----------


def test_recall_memory_finds_hits(registry: ToolRegistry, memory_store) -> None:
    memory_store.warm.add("therapist-elder-sister", "小林说过他最怕打雷")
    result = registry.execute(
        "recall_memory", json.dumps({"query": "打雷"}), _ctx(memory_store=memory_store)
    )
    assert result.success is True
    assert "打雷" in result.content
    assert result.data["memories"]


def test_recall_memory_no_hit(registry: ToolRegistry, memory_store) -> None:
    result = registry.execute(
        "recall_memory", json.dumps({"query": "不存在的词"}), _ctx(memory_store=memory_store)
    )
    assert result.success is True
    assert "暂时没有相关记忆" in result.content


def test_recall_memory_without_store(registry: ToolRegistry) -> None:
    result = registry.execute("recall_memory", json.dumps({"query": "x"}), _ctx())
    assert "不可用" in result.content


def test_context_carries_dependencies() -> None:
    ctx = _ctx(mood_store="M", memory_store="N")
    assert ctx.extras["mood_store"] == "M"
    assert ctx.extras["memory_store"] == "N"
    assert ctx.companion_id == "therapist-elder-sister"
