"""`study_skill` 工具测试（渐进式加载的「取」这一半）。

关键行为：被禁用的技能必须**同样报「不存在」**——否则用户关掉它，
模型反而能绕过开关把它取回来，界面上写的「已禁用」就成了假的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.skills.registry import SkillRegistry
from app.tools.registry import ToolContext, ToolRegistry
from app.tools.skills import CTX_SKILL_REGISTRY, STUDY_SKILL_NAME, build_study_skill_tool


def write_skill(root: Path, skill_id: str, *, name: str = "", body: str = "正文步骤") -> None:
    directory = root / skill_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name or skill_id}\ndescription: 说明\nwhen_to_use: 场景\n---\n\n{body}\n",
        encoding="utf-8",
    )


@pytest.fixture()
def skills(tmp_path: Path) -> SkillRegistry:
    root = tmp_path / "skills"
    write_skill(root, "crisis", name="情绪危机安抚", body="第一步：先接住情绪。")
    registry = SkillRegistry(state_path=tmp_path / "skills.json")
    registry.load(builtin_dir=root)
    return registry


@pytest.fixture()
def registry(skills: SkillRegistry) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(build_study_skill_tool())
    return registry


def _ctx(skills: SkillRegistry | None) -> ToolContext:
    extras = {} if skills is None else {CTX_SKILL_REGISTRY: skills}
    return ToolContext(companion_id="c1", extras=extras)


def test_study_returns_body(registry: ToolRegistry, skills: SkillRegistry) -> None:
    result = registry.execute(STUDY_SKILL_NAME, {"name": "crisis"}, _ctx(skills))

    assert result.success is True
    assert "第一步：先接住情绪。" in result.content
    assert "情绪危机安抚" in result.content   # 带上标题，模型知道自己在读哪一篇
    assert result.data["found"] is True


def test_study_unknown_skill_lists_alternatives(
    registry: ToolRegistry, skills: SkillRegistry
) -> None:
    """报错要**可恢复**：告诉模型有哪些可用，它下一步就能自我纠正。"""
    result = registry.execute(STUDY_SKILL_NAME, {"name": "nope"}, _ctx(skills))

    assert result.success is True          # 工具本身没坏，只是没这个技能
    assert result.data["found"] is False
    assert "crisis" in result.content      # 可用清单里能看到正确 id


def test_disabled_skill_is_treated_as_missing(
    registry: ToolRegistry, skills: SkillRegistry
) -> None:
    """禁用 = 取不到。否则开关形同虚设。"""
    skills.set_enabled("crisis", False)

    result = registry.execute(STUDY_SKILL_NAME, {"name": "crisis"}, _ctx(skills))

    assert result.data["found"] is False
    assert "第一步" not in result.content


def test_missing_registry_degrades_gracefully(registry: ToolRegistry) -> None:
    result = registry.execute(STUDY_SKILL_NAME, {"name": "crisis"}, _ctx(None))

    assert result.success is True
    assert result.data["found"] is False
    assert "不可用" in result.content


def test_empty_registry_reports_no_alternatives(registry: ToolRegistry, tmp_path: Path) -> None:
    empty = SkillRegistry(state_path=tmp_path / "s.json")

    result = registry.execute(STUDY_SKILL_NAME, {"name": "x"}, _ctx(empty))

    assert result.data["found"] is False
    assert "（无）" in result.content


def test_schema_exposes_name_argument(registry: ToolRegistry) -> None:
    schema = registry.get(STUDY_SKILL_NAME).to_openai_schema()

    params = schema["function"]["parameters"]
    assert "name" in params["properties"]
    assert params["required"] == ["name"]


def test_invalid_arguments_are_isolated(registry: ToolRegistry, skills: SkillRegistry) -> None:
    """缺必填参数时返回失败结果而不是抛异常（工具执行的统一隔离约定）。"""
    result = registry.execute(STUDY_SKILL_NAME, {}, _ctx(skills))

    assert result.success is False
    assert "参数不合法" in result.content
