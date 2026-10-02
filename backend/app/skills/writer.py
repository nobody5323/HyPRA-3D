"""技能落盘：把一份技能写成 `SKILL.md`，或把它从磁盘上删掉（`AGENTS.md §9.6` 的写入口）。

**只碰用户目录**（`USER_SKILLS_DIR`，默认 `data/skills`）：
`backend/skills/` 是随包分发的第一方内容，运行时既不改写也不删除——这与插件、
人设的口径一致（内置只读，要改就先复制/另存）。因此「删除内置技能」不在这里，
它由 `registry` 写进用户侧隐藏清单（包内文件一字未改，还能恢复）。

**写前校验、写后回读**：写进磁盘的那份文件必须能被宿主自己的 loader 解析出来，
否则删掉并报错。理由很实际——技能是「模型决定用它时才读正文」的机制，
一个解析不了的 SKILL.md 不会立刻报错，只会表现为「技能明明在列表里，模型却说不会」。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import yaml

from app.llm.authoring import validate_id
from app.skills.loader import SINGLE_FILE_SUFFIXES, SKILL_FILENAME, load_skill
from app.skills.models import Skill

logger = logging.getLogger(__name__)


class SkillWriteError(ValueError):
    """技能落盘被拒（id 非法 / 已存在 / 回读失败 / 删除越界 / 删除失败）。"""


def skill_dir(user_dir: str | Path, skill_id: str) -> Path:
    """技能包目录（`<user_dir>/<id>/`）。"""
    return Path(user_dir) / skill_id


def render_skill_markdown(
    *,
    skill_id: str,
    name: str,
    description: str,
    when_to_use: str,
    body: str,
) -> str:
    """渲染 SKILL.md 全文（frontmatter + 正文）。

    用 `yaml.safe_dump` 而不是手拼字符串：技能名与适用场景里常有冒号、引号、
    换行（中文标点尤其容易），手拼出来的 YAML 一旦被解释成别的结构，
    frontmatter 就会静默丢失。
    """
    meta = {
        "id": skill_id,
        "name": name,
        "description": description,
        "when_to_use": when_to_use,
    }
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{front}---\n\n{body.strip()}\n"


def save_skill(
    user_dir: str | Path,
    *,
    skill_id: str,
    name: str,
    description: str = "",
    when_to_use: str = "",
    body: str,
) -> Skill:
    """写入一个技能包并回读校验，返回装载后的 `Skill`。

    不做覆盖：同名已存在则拒绝（要改就是一次显式动作）。真要支持覆盖时，
    得走「先写暂存目录 → 回读通过 → 整体替换」，而不是先删后写——
    后者在替换失败时会把用户原有的技能弄丢。

    抛出:
        SkillWriteError: id 非法、目录已存在、正文为空、写入失败、
            或回读不通过（此时磁盘上的半成品会被清掉）。
    """
    try:
        safe_id = validate_id(skill_id, label="技能 id")
    except ValueError as exc:
        raise SkillWriteError(str(exc)) from exc

    if not body.strip():
        raise SkillWriteError("技能正文不能为空")

    target_dir = skill_dir(user_dir, safe_id)
    target = target_dir / SKILL_FILENAME
    if target.exists():
        raise SkillWriteError(f"技能 {safe_id} 已存在（换个 id，或先删掉它再写）")

    text = render_skill_markdown(
        skill_id=safe_id, name=name, description=description, when_to_use=when_to_use, body=body
    )
    # 先写临时文件再替换：半个文件被装载器读到的话，技能会「在列表里但读不出正文」。
    # 整段都包 try：目录创建失败（无权限 / 保留名 / 超长路径）也是可展示的错误，
    # 不该以裸 500 的形式冒出去。
    tmp = target.with_name(target.name + ".tmp")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        _cleanup(tmp)
        raise SkillWriteError(f"技能写入失败：{exc}") from exc

    skill = load_skill(target, source="user", default_id=safe_id)
    if skill is None:
        # 回读不通过：把刚写的东西删掉，绝不在磁盘上留一个装载器读不懂的技能
        _cleanup(target)
        raise SkillWriteError("写入的技能没能被解析器读回（已撤销），请检查正文是否为空")

    logger.info("技能已写入：%s", target)
    return skill


def delete_skill(user_dir: str | Path, skill: Skill) -> Path:
    """删掉一个**用户**技能，返回被删掉的路径。

    按装载时记下的 `skill.path` 定位，而不是拿 id 重新拼一遍路径：loader 接受
    `<id>/SKILL.md` 与 `<id>.md` 两种形态，重拼等于把「认哪种形态」写第二遍，
    两边迟早对不上（对不上的表现是「删不掉」或「删错东西」）。

    - 目录形：**整包删**——技能目录里可能放着附件，只删 `SKILL.md` 会留下一个
      装载器再也认不出的空壳；
    - 单文件形：删那一个 `.md`。

    只删用户目录里的东西：路径解析后必须仍落在 `user_dir` 内，否则拒绝——
    这是防 `..` 与符号链接逃逸的那道闸（id 校验挡不住 loader 记下的任意路径）。

    抛出:
        SkillWriteError: 未配置用户技能目录、目标不在用户目录内 / 形态不认识、
            或删除失败（被占用、无权限）。
    """
    if not str(user_dir).strip():
        raise SkillWriteError("未配置用户技能目录，无法删除技能")

    root = Path(user_dir).resolve()
    target = Path(skill.path).resolve()
    victim = _removable_path(root, target)
    if victim is None:
        raise SkillWriteError(
            f"拒绝删除 {target}：它不在用户技能目录（{root}）里，或不是技能形态"
        )

    try:
        if victim.is_dir():
            shutil.rmtree(victim)
        else:
            victim.unlink(missing_ok=True)
    except OSError as exc:
        raise SkillWriteError(f"技能删除失败：{exc}") from exc

    logger.info("技能已删除：%s", victim)
    return victim


def _removable_path(root: Path, target: Path) -> Path | None:
    """把「技能文件路径」翻译成「该删哪一个」；越界或不认识的形态返回 None。"""
    if target.name == SKILL_FILENAME and target.parent.parent == root:
        return target.parent  # 目录形
    if target.parent == root and target.suffix.lower() in SINGLE_FILE_SUFFIXES:
        return target  # 单文件形
    return None


def _cleanup(path: Path) -> None:
    """删掉半成品（清理失败只告警：它不该掩盖真正要报的那个错误）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - 清理失败只告警
        logger.warning("清理未成功：%s", path)
