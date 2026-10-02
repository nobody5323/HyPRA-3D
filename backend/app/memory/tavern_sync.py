"""酒馆对话 → 长期记忆的**同步入口**（宿主侧编排）。

为什么单独一层：需要**同一套**「读数据源 → 按角色定归属 → 增量导入」逻辑的调用方是——

- `GET /plugins/tavern-bridge/status`：算「待同步多少轮」（`pending_sync`）；
- 启动期自动同步（`TAVERN_SYNC_ON_STARTUP`，默认关，`sync_tavern_sessions`）。

（`POST /plugins/tavern-bridge/import` 自己拿数据源对象，因为它要先做
404/409 分支判断；但它复用同一个 `TavernMemoryImporter`，所以游标规则是同一套——
这才是「界面显示的数字 = 点下去实际写入的轮次」的保证。）

这几处各写一套的话，「说有 3 轮，点了却 0 轮」这类不一致用户根本无从判断是哪个环节错了。

归属（哪个陪伴对象）与**世界书的 `scope` 用同一个 id 函数**
（`scopes_by_character_name`）——两处各算各的，内容会挂在一个永远匹配不上的 id 上。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.memory.tavern_import import DEFAULT_STATE_FILE, ImportResult, TavernMemoryImporter
from app.plugins.contracts import DataSourceSnapshot
from app.plugins.datasources import read_snapshots
from app.prompts.persona.datasource_map import scopes_by_character_name

logger = logging.getLogger(__name__)


def _all_sessions(snapshots: list[DataSourceSnapshot]) -> list:
    return [session for snapshot in snapshots for session in snapshot.sessions]


def _character_scopes(snapshots: list[DataSourceSnapshot]) -> dict[str, str]:
    """把所有数据源的「角色名 → 陪伴对象 id」合成一张表（后者不覆盖前者）。"""
    scopes: dict[str, str] = {}
    for snapshot in snapshots:
        for name, scope in scopes_by_character_name(snapshot).items():
            scopes.setdefault(name, scope)
    return scopes


def pending_sync(
    *, companion_id: str, state_path: str | Path = DEFAULT_STATE_FILE
) -> tuple[int, int, list[str]]:
    """待同步统计 → `(轮次数, 会话数, 数据源警告)`。不写记忆。"""
    snapshots, warnings = read_snapshots()
    importer = TavernMemoryImporter(None, state_path=state_path)
    turns, sessions = importer.pending_progress(
        _all_sessions(snapshots),
        companion_id=companion_id,
        character_scopes=_character_scopes(snapshots),
    )
    return turns, sessions, warnings


def sync_tavern_sessions(
    memory_store,
    *,
    companion_id: str,
    state_path: str | Path = DEFAULT_STATE_FILE,
    force: bool = False,
) -> tuple[ImportResult, list[str]]:
    """读数据源 → 按角色定归属 → 增量导入，返回 `(导入结果, 数据源警告)`。

    参数:
        companion_id: **回落**作用域——匹配不到角色卡的会话写到这里。
    """
    snapshots, warnings = read_snapshots()
    sessions = _all_sessions(snapshots)
    if not sessions:
        return ImportResult(), warnings
    importer = TavernMemoryImporter(memory_store, state_path=state_path)
    result = importer.import_sessions(
        sessions,
        companion_id=companion_id,
        character_scopes=_character_scopes(snapshots),
        force=force,
    )
    return result, warnings
