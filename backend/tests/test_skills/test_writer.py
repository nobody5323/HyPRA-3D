"""技能写入与删除测试（`app/skills/writer.py`）。

写入是本轮新增的唯一「技能落盘」路径，因此这里盯住四件事：

1. 写出来的文件**能被 loader 读回**（写后回读校验不是装饰）；
2. 只写用户目录，且不覆盖已存在的技能；
3. 非法输入（id、空正文）被拒且**不留半成品**；
4. frontmatter 里的中文与标点不会把 YAML 弄坏（技能名里常有冒号）。

删除（`delete_skill`）盯另外三件事：

5. 两种形态（`<id>/SKILL.md` 与 `<id>.md`）都删得掉，目录形**整包删**（附件跟着走）；
6. **只删用户目录里的东西**——loader 记下的路径一旦越界就拒绝（防 `..` / 符号链接逃逸）；
7. 删完 loader 扫不到（否则「删了还在」比不删更让人困惑）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.skills.loader import SKILL_FILENAME, load_skill, scan_skills
from app.skills.models import Skill
from app.skills.writer import SkillWriteError, delete_skill, save_skill


def _save(tmp_path: Path, **overrides):
    params = {
        "skill_id": "calm-down",
        "name": "降火",
        "description": "先把火气降下来再谈事",
        "when_to_use": "用户明显在气头上、开始指责他人时",
        "body": "# 降火\n\n## 步骤\n1. 先复述对方的原话。\n2. 不评判、不解释。\n",
    }
    params.update(overrides)
    return save_skill(tmp_path, **params)


def test_write_then_load_round_trip(tmp_path: Path) -> None:
    """写进去的技能必须能被宿主自己的 loader 读回来。"""
    skill = _save(tmp_path)

    assert skill.id == "calm-down"
    assert skill.source == "user"
    assert skill.body.startswith("# 降火")

    # 磁盘上确实落了 SKILL.md，且能被独立读回（不依赖写入时的对象）
    path = tmp_path / "calm-down" / SKILL_FILENAME
    assert path.is_file()
    reloaded = load_skill(path, source="user", default_id="calm-down")
    assert reloaded is not None
    assert reloaded.when_to_use == "用户明显在气头上、开始指责他人时"


def test_frontmatter_keeps_colons_and_quotes(tmp_path: Path) -> None:
    """技能名/场景里的冒号与引号不该把 YAML 结构弄坏（手拼字符串必翻车的那种）。"""
    _save(
        tmp_path,
        skill_id="quoted",
        name='别急着说「你应该」: 先听',
        when_to_use='用户说「我不知道」时; 或反复问"我是不是很糟"',
    )

    reloaded = load_skill(tmp_path / "quoted" / SKILL_FILENAME, source="user", default_id="quoted")
    assert reloaded is not None
    assert reloaded.name == '别急着说「你应该」: 先听'
    assert "我是不是很糟" in reloaded.when_to_use


def test_refuses_existing_skill(tmp_path: Path) -> None:
    """不静默覆盖用户已有的技能（要改就是一次显式动作）。"""
    _save(tmp_path)

    with pytest.raises(SkillWriteError) as excinfo:
        _save(tmp_path, body="# 另一个版本\n\n内容不同。\n")

    assert "已存在" in str(excinfo.value)


def test_refuses_bad_id_and_empty_body(tmp_path: Path) -> None:
    with pytest.raises(SkillWriteError):
        _save(tmp_path, skill_id="Not A Skill")

    with pytest.raises(SkillWriteError):
        _save(tmp_path, skill_id="blank", body="   ")


def test_rollback_when_readback_fails(tmp_path: Path, monkeypatch) -> None:
    """回读不通过时把刚写的文件删掉：绝不留一个装载器读不懂的技能。"""
    import app.skills.writer as writer

    monkeypatch.setattr(writer, "load_skill", lambda *_, **__: None)

    with pytest.raises(SkillWriteError) as excinfo:
        _save(tmp_path)

    assert "没能被解析器读回" in str(excinfo.value)
    assert not (tmp_path / "calm-down" / SKILL_FILENAME).exists()


# =============================================================
# 删除
# =============================================================


def test_delete_removes_whole_skill_dir(tmp_path: Path) -> None:
    """目录形整包删：技能目录里可能放着附件，留个空壳等于留一个读不出的技能。"""
    skill = _save(tmp_path)
    (tmp_path / "calm-down" / "steps.md").write_text("附件", encoding="utf-8")

    removed = delete_skill(tmp_path, skill)

    assert removed == (tmp_path / "calm-down").resolve()
    assert not (tmp_path / "calm-down").exists()
    assert scan_skills(tmp_path, source="user") == []


def test_delete_removes_single_file_skill(tmp_path: Path) -> None:
    """单文件形（用户手写时的轻量形态）也要删得掉。"""
    path = tmp_path / "quick.md"
    path.write_text("---\nname: 随手记\n---\n\n正文\n", encoding="utf-8")
    skill = load_skill(path, source="user", default_id="quick")

    removed = delete_skill(tmp_path, skill)

    assert removed == path.resolve()
    assert not path.exists()
    assert scan_skills(tmp_path, source="user") == []


def test_delete_refuses_path_outside_user_dir(tmp_path: Path) -> None:
    """越界拒绝：loader 记下的路径不受 id 校验约束，删除必须自己再验一次。"""
    user = tmp_path / "user"
    user.mkdir()
    outside = tmp_path / "elsewhere" / SKILL_FILENAME
    outside.parent.mkdir()
    outside.write_text("---\nname: 别人的\n---\n\n正文\n", encoding="utf-8")
    skill = load_skill(outside, source="user", default_id="elsewhere")

    with pytest.raises(SkillWriteError, match="拒绝删除"):
        delete_skill(user, skill)

    assert outside.is_file()  # 一个字都没动


def test_delete_refuses_without_user_dir(tmp_path: Path) -> None:
    """没配用户技能目录时宁可拒绝：否则 `Path("")` 会指向当前工作目录。"""
    skill = _save(tmp_path)

    with pytest.raises(SkillWriteError, match="用户技能目录"):
        delete_skill("", skill)

    assert (tmp_path / "calm-down" / SKILL_FILENAME).is_file()


def test_delete_is_idempotent_on_disk(tmp_path: Path) -> None:
    """文件已经不在（用户手删过）时不该报错——目标已达成。"""
    skill = _save(tmp_path)
    skill_path = tmp_path / "calm-down" / SKILL_FILENAME
    skill_path.unlink()

    removed = delete_skill(tmp_path, skill)

    assert removed == (tmp_path / "calm-down").resolve()
    assert not (tmp_path / "calm-down").exists()


def test_delete_rejects_arbitrary_path_shape(tmp_path: Path) -> None:
    """不是技能形态的路径不删（比如有人把 path 指向一个普通目录）。"""
    user = tmp_path / "user"
    user.mkdir()
    stray = user / "notes.txt"
    stray.write_text("不是技能", encoding="utf-8")
    skill = Skill(
        id="stray",
        name="stray",
        description="",
        when_to_use="",
        body="正文",
        source="user",
        path=stray,
    )

    with pytest.raises(SkillWriteError, match="拒绝删除"):
        delete_skill(user, skill)

    assert stray.is_file()
