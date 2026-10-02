"""`SKILL.md` 的解析与目录扫描。

文件格式（与 pi 的 skills 同范式，两边内容可直接互通）：

    ---
    name: 主动倾听
    description: 先复述确认理解，再决定要不要给建议
    when_to_use: 用户在讲述一件具体的事、抱怨，或情绪明显但并未求助时
    ---
    # Markdown 正文
    ...

容错原则：技能文件是**用户可手写**的，格式错误不该让整个技能库空掉——
单个文件解析失败只警告并跳过它，其余照常装载。
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.skills.models import Skill

logger = logging.getLogger(__name__)

#: 技能文件的固定名（与 pi / Claude skills 一致）
SKILL_FILENAME = "SKILL.md"

#: frontmatter 分隔符
_FENCE = "---"

#: 接受为「单文件技能」的扩展名（用户不一定愿意为一条说明建目录）。
#: 公开而非私有：删除（`writer.delete_skill`）必须认出**同一个**集合，
#: 否则会出现「装载器认它、删除说它不是技能」这种自相矛盾。
SINGLE_FILE_SUFFIXES = frozenset({".md", ".markdown"})


def split_frontmatter(text: str) -> tuple[dict, str]:
    """拆出 YAML frontmatter 与正文。

    没有 frontmatter 时返回 `({}, 全文)`——忘了写头部不该让整个文件作废，
    调用方会用目录名兜底当 id。
    """
    stripped = text.lstrip("\ufeff")  # 容忍 BOM（Windows 编辑器常见）
    if not stripped.startswith(_FENCE):
        return {}, stripped.strip()

    lines = stripped.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == _FENCE:
            raw = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :]).strip()
            try:
                meta = yaml.safe_load(raw) or {}
            except yaml.YAMLError as exc:
                logger.warning("技能 frontmatter 解析失败（按无头部处理）：%s", exc)
                return {}, stripped.strip()
            return (meta if isinstance(meta, dict) else {}), body

    # 只有开头的 --- 而没有收尾：当作没有 frontmatter
    return {}, stripped.strip()


def load_skill(path: Path, *, source: str, default_id: str = "") -> Skill | None:
    """从单个 Markdown 文件载入技能。

    `default_id` 是 frontmatter 没写 `id` 时的兜底（目录名或文件名主干）。
    文件不可读、或没有正文时返回 None（跳过而不是让装载失败）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("技能文件读取失败（%s）：%s", path, exc)
        return None

    meta, body = split_frontmatter(text)
    skill_id = str(meta.get("id") or default_id).strip()
    if not skill_id or not body:
        logger.warning("技能缺少 id 或正文，已跳过：%s", path)
        return None

    return Skill(
        id=skill_id,
        name=str(meta.get("name") or skill_id).strip(),
        description=str(meta.get("description") or "").strip(),
        when_to_use=str(meta.get("when_to_use") or "").strip(),
        body=body,
        source=source,
        path=path,
    )


def scan_skills(directory: str | Path, *, source: str) -> list[Skill]:
    """扫描技能目录。

    两种形态都接受：
    - `<id>/SKILL.md`（推荐：正文与文件分离，便于放附件）；
    - `<id>.md`（用户手写时的轻量形态）。
    """
    root = Path(directory)
    # 空路径必须提前拦住：Path("") 等于当前目录，会意外扫描整个工作目录
    if not str(directory).strip() or not root.is_dir():
        return []

    found: list[Skill] = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            candidate = child / SKILL_FILENAME
            if candidate.is_file():
                skill = load_skill(candidate, source=source, default_id=child.name)
                if skill is not None:
                    found.append(skill)
        elif child.suffix.lower() in SINGLE_FILE_SUFFIXES:
            skill = load_skill(child, source=source, default_id=child.stem)
            if skill is not None:
                found.append(skill)
    return found
