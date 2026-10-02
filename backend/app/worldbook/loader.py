"""世界书条目加载器：YAML 与代码分离（同 persona 预设约定）。

条目文件为纯 YAML（见 entries/），本模块负责读取并用 pydantic 校验。
"""

from pathlib import Path

import yaml

from app.paths import resource_path
from app.worldbook.models import WorldBookEntry


def entries_dir() -> Path:
    """内置世界书条目目录（随程序分发的只读资源）。"""
    return resource_path("app", "worldbook", "entries")


def load_entry_file(file_path: str | Path) -> WorldBookEntry:
    """加载并校验单个世界书条目 YAML。"""
    path = Path(file_path)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"世界书文件 {path.name} 顶层必须是映射（dict）")
    entry = WorldBookEntry.model_validate(raw)
    if not entry.has_trigger:
        raise ValueError(
            f"世界书条目 {entry.id} 缺少触发条件"
            "（keys / regex / vector_text 至少一项，或 constant: true）"
        )
    return entry


def load_builtin_entries() -> list[WorldBookEntry]:
    """加载 entries/ 目录下全部 *.yaml，按文件名字母序返回列表。"""
    entries: list[WorldBookEntry] = []
    for file_path in sorted(entries_dir().glob("*.yaml")):
        entries.append(load_entry_file(file_path))
    return entries
