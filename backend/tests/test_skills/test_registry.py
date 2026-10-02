"""技能注册表测试：装载优先级、启停落盘、删除/隐藏、清单渲染。

两个反复出现的关注点：
1. **清单不含正文**——清单是常驻上下文的，正文只在 `study_skill` 时出现；
2. **启停要落盘**——不落盘的话重启即回弹，用户的操作看起来没生效（与插件同理由）。

删除有两条路（§9.6）：自建技能**物理删除**（那是用户自己的文件），
内置技能只写**隐藏清单**（随包分发的第一方内容运行时永不删改，且能恢复）。
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


def make_pair(dirs, **kwargs) -> tuple[SkillRegistry, Path]:
    """装载好内置 + 用户两个目录的注册表，连带返回状态文件路径。"""
    builtin, user = dirs
    state_path = builtin.parent / "skills.json"
    registry = SkillRegistry(state_path=state_path, **kwargs)
    registry.load(builtin_dir=builtin, user_dir=user)
    return registry, state_path


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


# ---------- 删除与恢复 ----------


def test_delete_builtin_hides_it_but_keeps_the_file(dirs) -> None:
    """内置技能删除 = 写隐藏清单：包内文件一字未改，还能恢复（§9.12 硬规则 2）。"""
    builtin, user = dirs
    path = write_skill(builtin, "crisis")
    registry, state_path = make_pair(dirs)

    skill, removed = registry.delete("crisis")

    assert skill.id == "crisis"
    assert removed is None
    assert path.is_file()  # 文件还在
    assert registry.all() == []
    assert [item.id for item in registry.hidden()] == ["crisis"]
    assert json.loads(state_path.read_text(encoding="utf-8"))["deleted"] == ["crisis"]


def test_deleted_skill_is_gone_from_every_chat_facing_view(dirs) -> None:
    """删掉的技能不能因为「文件还在」就继续出现在模型眼前。"""
    builtin, user = dirs
    write_skill(builtin, "crisis", body="第一步：先接住情绪")
    write_skill(builtin, "other")
    registry, _ = make_pair(dirs)

    registry.delete("crisis")

    assert [item.id for item in registry.enabled()] == ["other"]
    assert registry.get("crisis") is None  # 详情 / study_skill 都取不到
    assert registry.is_enabled("crisis") is False
    assert "crisis" not in registry.catalog_text()
    assert len(registry) == 1


def test_delete_user_skill_removes_files(dirs) -> None:
    """自建技能是用户自己的文件：删除就是物理删除。"""
    builtin, user = dirs
    write_skill(user, "mine")
    (user / "mine" / "steps.md").write_text("附件", encoding="utf-8")
    registry, state_path = make_pair(dirs)

    _, removed = registry.delete("mine")

    assert removed == (user / "mine").resolve()
    assert not (user / "mine").exists()
    assert registry.get("mine") is None
    # 自建技能不需要隐藏记录：文件没了，重扫也不会回来
    assert "deleted" not in json.loads(state_path.read_text(encoding="utf-8"))


def test_hidden_builtin_survives_reload(dirs) -> None:
    """隐藏要落盘：重启（或点「重新扫描」）后不能自己冒回来。"""
    builtin, user = dirs
    write_skill(builtin, "crisis")
    write_skill(builtin, "other")
    registry, state_path = make_pair(dirs)
    registry.delete("crisis")

    restarted = SkillRegistry(state_path=state_path)
    restarted.load(builtin_dir=builtin, user_dir=user)

    assert [item.id for item in restarted.all()] == ["other"]
    assert [item.id for item in restarted.hidden()] == ["crisis"]


def test_restore_brings_hidden_builtin_back(dirs) -> None:
    builtin, user = dirs
    write_skill(builtin, "crisis")
    registry, state_path = make_pair(dirs)
    registry.delete("crisis")

    skill = registry.restore("crisis")

    assert skill.id == "crisis"
    assert [item.id for item in registry.all()] == ["crisis"]
    assert registry.hidden() == []
    assert "deleted" not in json.loads(state_path.read_text(encoding="utf-8"))


def test_restore_keeps_the_disabled_preference(dirs) -> None:
    """先禁用、再删除、之后恢复：应当回到「禁用」，而不是被顺手改成启用。"""
    builtin, user = dirs
    write_skill(builtin, "crisis")
    registry, _ = make_pair(dirs)
    registry.set_enabled("crisis", False)
    registry.delete("crisis")

    registry.restore("crisis")

    assert registry.is_enabled("crisis") is False
    assert registry.enabled() == []


def test_deleting_user_skill_clears_its_disabled_record(dirs) -> None:
    """自建技能删掉后禁用记录必须一起清：否则同 id 新建的技能会一出生就是禁用的。"""
    builtin, user = dirs
    write_skill(user, "mine")
    registry, state_path = make_pair(dirs)
    registry.set_enabled("mine", False)
    registry.delete("mine")

    # 用户用同一个 id 又写了一份
    write_skill(user, "mine", name="重写的")
    fresh = SkillRegistry(state_path=state_path)
    fresh.load(builtin_dir=builtin, user_dir=user)

    assert [item.id for item in fresh.enabled()] == ["mine"]


def test_delete_and_restore_reject_bad_ids(dirs) -> None:
    builtin, user = dirs
    write_skill(builtin, "crisis")
    registry, _ = make_pair(dirs)

    with pytest.raises(KeyError, match="技能不存在"):
        registry.delete("nope")
    # 自建技能删了就是删了，没有可恢复的对象
    with pytest.raises(KeyError, match="不在隐藏清单"):
        registry.restore("crisis")
    # 已在隐藏清单里的再删一次 = 不存在
    registry.delete("crisis")
    with pytest.raises(KeyError, match="技能不存在"):
        registry.delete("crisis")


def test_hidden_id_is_still_reserved_for_builtin(dirs) -> None:
    """隐藏的内置技能仍占着 id（装载时内置优先），查重必须看得见它。"""
    builtin, user = dirs
    write_skill(builtin, "crisis", name="内置版")
    registry, _ = make_pair(dirs)
    registry.delete("crisis")

    assert registry.get("crisis") is None
    assert registry.get_including_hidden("crisis").name == "内置版"


def test_status_marks_deleted_entries(dirs) -> None:
    """列表含隐藏项（`deleted: true`），界面靠它列出「已隐藏」并提供恢复。"""
    builtin, user = dirs
    write_skill(builtin, "crisis")
    write_skill(user, "mine")
    registry, _ = make_pair(dirs)

    registry.delete("crisis")
    by_id = {entry["id"]: entry for entry in registry.status()}

    assert by_id["crisis"]["deleted"] is True
    assert by_id["crisis"]["enabled"] is False
    assert by_id["mine"]["deleted"] is False


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
