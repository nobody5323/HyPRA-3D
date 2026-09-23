"""技能注册表测试：装载优先级、启停落盘、清单渲染。

两个反复出现的关注点：
1. **清单不含正文**——清单是常驻上下文的，正文只在 `study_skill` 时出现；
2. **启停要落盘**——不落盘的话重启即回弹，用户的操作看起来没生效（与插件同理由）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.skills.registry import SkillRegistry


def write_skill(root: Path, skill_id: str, *, name: str = "", body: str = "正文内容") -> Path:
    """在 `<root>/<id>/SKILL.md` 写一个技能。"""
    directory = root / skill_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(
        f"---\nname: {name or skill_id}\ndescription: 说明\n"
        f"when_to_use: 适用场景\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    return builtin, user


def make_registry(tmp_path: Path, **kwargs) -> SkillRegistry:
    return SkillRegistry(state_path=tmp_path / "skills.json", **kwargs)


# ---------- 装载 ----------


def test_load_reads_both_directories(dirs) -> None:
    builtin, user = dirs
    write_skill(builtin, "crisis")
    write_skill(user, "mine")

    registry = make_registry(dirs[0])
    assert registry.load(builtin_dir=builtin, user_dir=user) == 2
    assert {skill.id for skill in registry.all()} == {"crisis", "mine"}
    assert registry.get("crisis").source == "builtin"
    assert registry.get("mine").source == "user"


def test_builtin_wins_on_id_collision(dirs) -> None:
    """用户放一个同名技能，不该悄悄顶掉随项目分发的版本。"""
    builtin, user = dirs
    write_skill(builtin, "crisis", name="内置版", body="内置正文")
    write_skill(user, "crisis", name="用户版", body="用户正文")

    registry = make_registry(dirs[0])
    registry.load(builtin_dir=builtin, user_dir=user)

    assert len(registry) == 1
    assert registry.get("crisis").name == "内置版"


def test_load_without_any_directory_is_empty(tmp_path: Path) -> None:
    registry = make_registry(tmp_path)
    assert registry.load() == 0
    assert registry.all() == []


# ---------- 启停与落盘 ----------


def test_disable_persists_and_survives_reload(dirs) -> None:
    builtin, user = dirs
    write_skill(builtin, "a")
    write_skill(builtin, "b")
    state_path = builtin.parent / "skills.json"

    first = SkillRegistry(state_path=state_path)
    first.load(builtin_dir=builtin, user_dir=user)
    first.set_enabled("a", False)

    assert json.loads(state_path.read_text(encoding="utf-8")) == {"disabled": ["a"]}

    # 模拟重启：新建实例读同一份状态
    second = SkillRegistry(state_path=state_path)
    second.load(builtin_dir=builtin, user_dir=user)
    assert [skill.id for skill in second.enabled()] == ["b"]
    assert second.is_enabled("a") is False


def test_config_defaults_apply_only_without_state_file(dirs) -> None:
    builtin, user = dirs
    write_skill(builtin, "a")
    write_skill(builtin, "b")
    state_path = builtin.parent / "skills.json"

    registry = SkillRegistry(state_path=state_path)
    registry.load(builtin_dir=builtin, user_dir=user, default_disabled=["a"])
    assert [skill.id for skill in registry.enabled()] == ["b"]

    # 状态文件一旦存在，就以它为准（用户的选择覆盖配置初始值）
    registry.set_enabled("a", True)
    reloaded = SkillRegistry(state_path=state_path)
    reloaded.load(builtin_dir=builtin, user_dir=user, default_disabled=["a"])
    assert [skill.id for skill in reloaded.enabled()] == ["a", "b"]


def test_corrupt_state_falls_back_to_defaults(dirs) -> None:
    builtin, user = dirs
    write_skill(builtin, "a")
    state_path = builtin.parent / "skills.json"
    state_path.write_text("{ 不是 JSON", encoding="utf-8")

    registry = SkillRegistry(state_path=state_path)
    registry.load(builtin_dir=builtin, user_dir=user)

    assert [skill.id for skill in registry.enabled()] == ["a"]


def test_non_list_disabled_field_falls_back(dirs) -> None:
    builtin, user = dirs
    write_skill(builtin, "a")
    state_path = builtin.parent / "skills.json"
    state_path.write_text('{"disabled": "a"}', encoding="utf-8")

    registry = SkillRegistry(state_path=state_path)
    registry.load(builtin_dir=builtin, user_dir=user, default_disabled=["a"])

    assert registry.is_enabled("a") is False


def test_set_enabled_unknown_id_raises(dirs) -> None:
    builtin, user = dirs
    registry = make_registry(builtin)
    registry.load(builtin_dir=builtin, user_dir=user)

    with pytest.raises(KeyError, match="技能不存在"):
        registry.set_enabled("nope", False)


# ---------- 常驻清单 ----------


def test_catalog_lists_id_name_and_when_to_use_only(dirs) -> None:
    builtin, user = dirs
    write_skill(builtin, "crisis", name="情绪危机安抚", body="第一步：先接住情绪")

    registry = make_registry(builtin)
    registry.load(builtin_dir=builtin, user_dir=user)
    catalog = registry.catalog_text()

    assert "crisis" in catalog
    assert "情绪危机安抚" in catalog
    assert "适用场景" in catalog
    # 正文不进清单——那是 study_skill 的事
    assert "第一步" not in catalog


def test_catalog_empty_without_enabled_skills(dirs) -> None:
    """没有启用技能时返回空串，调用方据此整层跳过（不注入空标题）。"""
    builtin, user = dirs
    write_skill(builtin, "a")

    registry = make_registry(builtin)
    registry.load(builtin_dir=builtin, user_dir=user)
    assert registry.catalog_text() != ""

    registry.set_enabled("a", False)
    assert registry.catalog_text() == ""


def test_catalog_truncates_on_budget(dirs) -> None:
    """清单超预算时从尾部截断——宁可少列几个技能，也不让索引吃掉上下文。"""
    builtin, user = dirs
    write_skill(builtin, "a", name="甲")
    write_skill(builtin, "b", name="乙")

    registry = make_registry(builtin)
    registry.load(builtin_dir=builtin, user_dir=user)

    assert len(registry.catalog_text(budget=10_000).splitlines()) == 2
    assert len(registry.catalog_text(budget=1).splitlines()) == 0


# ---------- 列表视图 ----------


def test_status_excludes_body(dirs) -> None:
    """列表接口不该把所有技能正文都吐出去——长正文可能上万 token。"""
    builtin, user = dirs
    write_skill(builtin, "a", body="很长的正文" * 100)

    registry = make_registry(builtin)
    registry.load(builtin_dir=builtin, user_dir=user)
    entry = registry.status()[0]

    assert entry["id"] == "a"
    assert entry["enabled"] is True
    assert entry["body_chars"] > 0
    assert "body" not in entry
