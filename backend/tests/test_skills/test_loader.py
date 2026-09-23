"""`SKILL.md` 解析与扫描测试。

技能文件是**用户可手写**的，所以这里的重点不是「正常路径」，而是**容错**：
BOM、忘写 frontmatter、YAML 写坏、只有一条分隔线、没正文……都不该让整个技能库空掉。
"""

from __future__ import annotations

from pathlib import Path

from app.skills.loader import load_skill, scan_skills, split_frontmatter

FULL = """---
name: 主动倾听
description: 先复述再给建议
when_to_use: 用户在讲述一件事时
---

# 正文

第一步……
"""


# ---------- frontmatter 拆分 ----------


def test_splits_frontmatter_and_body() -> None:
    meta, body = split_frontmatter(FULL)

    assert meta["name"] == "主动倾听"
    assert meta["when_to_use"] == "用户在讲述一件事时"
    assert body.startswith("# 正文")
    assert "---" not in body


def test_without_frontmatter_keeps_whole_text() -> None:
    """忘了写头部不该让文件作废——调用方会用文件名兜底当 id。"""
    meta, body = split_frontmatter("# 只有正文\n\n内容")

    assert meta == {}
    assert body == "# 只有正文\n\n内容"


def test_tolerates_bom() -> None:
    """Windows 编辑器存出来的 UTF-8 BOM 很常见。"""
    meta, body = split_frontmatter("\ufeff" + FULL)

    assert meta["name"] == "主动倾听"
    assert body.startswith("# 正文")


def test_unclosed_frontmatter_treated_as_body() -> None:
    """只有开头的 `---` 而没有收尾：当作没有 frontmatter，而不是把正文吃掉。"""
    meta, body = split_frontmatter("---\nname: 半截\n\n# 正文")

    assert meta == {}
    assert "# 正文" in body


def test_broken_yaml_falls_back_to_body() -> None:
    meta, body = split_frontmatter("---\nname: [未闭合\n---\n\n正文")

    assert meta == {}
    assert "正文" in body


def test_non_mapping_frontmatter_ignored() -> None:
    """frontmatter 是个列表（而不是键值对）时按无头部处理。"""
    meta, body = split_frontmatter("---\n- a\n- b\n---\n\n正文")

    assert meta == {}
    assert body == "正文"


# ---------- 载入 ----------


def test_load_skill_reads_metadata(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(FULL, encoding="utf-8")

    skill = load_skill(path, source="user", default_id="active-listening")

    assert skill is not None
    assert skill.id == "active-listening"
    assert skill.name == "主动倾听"
    assert skill.description == "先复述再给建议"
    assert skill.when_to_use == "用户在讲述一件事时"
    assert skill.source == "user"
    assert skill.body.startswith("# 正文")


def test_frontmatter_id_wins_over_default(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("---\nid: custom-id\nname: 甲\n---\n\n正文", encoding="utf-8")

    skill = load_skill(path, source="builtin", default_id="dir-name")

    assert skill is not None
    assert skill.id == "custom-id"


def test_missing_body_returns_none(tmp_path: Path) -> None:
    """只有头部没有正文的技能没有意义——跳过它，而不是塞进清单里。"""
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: 空技能\n---\n\n   \n", encoding="utf-8")

    assert load_skill(path, source="user", default_id="x") is None


def test_unreadable_file_returns_none(tmp_path: Path) -> None:
    assert load_skill(tmp_path / "nope.md", source="user", default_id="x") is None


# ---------- 目录扫描 ----------


def test_scan_finds_directory_and_single_file_forms(tmp_path: Path) -> None:
    """两种形态都接受：`<id>/SKILL.md`（推荐）与 `<id>.md`（手写轻量形态）。"""
    nested = tmp_path / "crisis" / "SKILL.md"
    nested.parent.mkdir()
    nested.write_text("---\nname: 危机\n---\n\n正文", encoding="utf-8")
    (tmp_path / "listening.md").write_text("---\nname: 倾听\n---\n\n正文", encoding="utf-8")

    found = {skill.id: skill for skill in scan_skills(tmp_path, source="user")}

    assert set(found) == {"crisis", "listening"}
    assert found["listening"].path.name == "listening.md"


def test_scan_ignores_unrelated_files(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("不是技能", encoding="utf-8")
    (tmp_path / "empty-dir").mkdir()

    assert scan_skills(tmp_path, source="user") == []


def test_scan_missing_directory_is_empty(tmp_path: Path) -> None:
    assert scan_skills(tmp_path / "nope", source="user") == []


def test_scan_empty_path_does_not_walk_cwd() -> None:
    """空路径必须直接返回。

    `Path("")` 等于当前目录——不拦住的话会把工作目录整个扫一遍，
    在用户没配 `USER_SKILLS_DIR` 时表现为「技能库莫名多出一堆东西」。
    """
    assert scan_skills("", source="user") == []
    assert scan_skills("   ", source="user") == []


def test_scan_skips_broken_skill_but_keeps_others(tmp_path: Path) -> None:
    good = tmp_path / "good" / "SKILL.md"
    good.parent.mkdir()
    good.write_text("---\nname: 好的\n---\n\n正文", encoding="utf-8")
    bad = tmp_path / "bad" / "SKILL.md"
    bad.parent.mkdir()
    bad.write_text("---\nname: 坏的\n---\n\n", encoding="utf-8")

    ids = [skill.id for skill in scan_skills(tmp_path, source="user")]

    assert ids == ["good"]
