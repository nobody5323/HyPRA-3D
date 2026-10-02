"""酒馆同步入口测试：待同步统计 + 增量同步（状态接口与启动期共用同一套）。

这一层的价值就是**一致性**：界面上显示「有 N 轮待同步」，点下去就该写 N 轮。
所以这里的断言重点是「两个数字相等」，而不是各自单测。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.tavern_import import TavernMemoryImporter
from app.memory.tavern_sync import pending_sync, sync_tavern_sessions
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.plugins.contracts import (
    DataSourceCharacter,
    DataSourceMessage,
    DataSourceSession,
    DataSourceSnapshot,
)


class _FakeDataSource:
    """最小数据源替身：只有 `read()`。"""

    def __init__(self, snapshot: DataSourceSnapshot) -> None:
        self._snapshot = snapshot

    def read(self) -> DataSourceSnapshot:
        return self._snapshot


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(
        warm=InMemoryWarmStore(),
        cold=SqliteColdStore(db_path=tmp_path / "memory.db"),
    )


def _session(session_id: str, pairs: list[tuple[str, str]], who: str) -> DataSourceSession:
    messages: list[DataSourceMessage] = []
    for user_text, assistant_text in pairs:
        if user_text:
            messages.append(DataSourceMessage(role="user", text=user_text))
        if assistant_text:
            messages.append(DataSourceMessage(role="assistant", text=assistant_text))
    return DataSourceSession(
        id=session_id, title=session_id, character_name=who, messages=tuple(messages)
    )


def _card(name: str) -> DataSourceCharacter:
    return DataSourceCharacter(
        name=name, description="设定正文", source="demo-source", source_id=name
    )


def _install(tmp_path: Path, snapshot: DataSourceSnapshot):
    """装上只含一个假数据源的插件管理器，返回原管理器。"""
    from app.plugins.manager import PluginManager, get_plugin_manager, set_plugin_manager
    from app.plugins.manifest import PluginManifest
    from app.plugins.registry import PluginRegistration, PluginRegistry

    registry = PluginRegistry()
    registry.register(
        PluginRegistration(
            manifest=PluginManifest(id="demo-source", display_name="示例数据源"),
            datasources=[_FakeDataSource(snapshot)],
        )
    )
    original = get_plugin_manager()
    set_plugin_manager(PluginManager(registry, data_dir=tmp_path, plugin_dirs=[]))
    return original


def _snapshot(*sessions: DataSourceSession) -> DataSourceSnapshot:
    names = {session.character_name for session in sessions}
    return DataSourceSnapshot(
        sessions=list(sessions),
        characters=[_card(name) for name in sorted(names)],
    )


# =============================================================
# 待同步统计
# =============================================================


def test_pending_counts_everything_on_first_run(tmp_path) -> None:
    snapshot = _snapshot(
        _session("s1", [("甲", "乙"), ("丙", "丁")], "角色甲"),
        _session("s2", [("戊", "己")], "角色乙"),
    )
    original = _install(tmp_path, snapshot)
    try:
        turns, sessions, warnings = pending_sync(
            companion_id="fallback", state_path=tmp_path / "import.json"
        )
    finally:
        from app.plugins.manager import set_plugin_manager

        set_plugin_manager(original)

    assert (turns, sessions) == (3, 2)
    assert warnings == []


def test_pending_is_zero_without_datasources(tmp_path) -> None:
    original = _install(tmp_path, DataSourceSnapshot())
    try:
        assert pending_sync(
            companion_id="fallback", state_path=tmp_path / "import.json"
        ) == (0, 0, [])
    finally:
        from app.plugins.manager import set_plugin_manager

        set_plugin_manager(original)


def test_pending_matches_what_sync_actually_writes(tmp_path, store) -> None:
    """★ 界面上的数字必须等于点下去的写入量。"""
    snapshot = _snapshot(
        _session("s1", [("甲", "乙"), ("丙", "丁")], "角色甲"),
        _session("s2", [("戊", "己")], "角色乙"),
    )
    state_path = tmp_path / "import.json"
    original = _install(tmp_path, snapshot)
    try:
        pending_turns, pending_sessions, _ = pending_sync(
            companion_id="fallback", state_path=state_path
        )
        result, _ = sync_tavern_sessions(
            store, companion_id="fallback", state_path=state_path
        )
        after_turns, after_sessions, _ = pending_sync(
            companion_id="fallback", state_path=state_path
        )
    finally:
        from app.plugins.manager import set_plugin_manager

        set_plugin_manager(original)

    assert (pending_turns, pending_sessions) == (3, 2)
    assert (result.turns, result.sessions_imported) == (pending_turns, pending_sessions)
    assert (after_turns, after_sessions) == (0, 0)  # 同步完就清了
    assert result.warnings == []


# =============================================================
# 增量同步
# =============================================================


def test_sync_picks_up_new_turns_only(tmp_path, store) -> None:
    """★ 酒馆里接着聊的新内容：下次同步只补新增的轮次。"""
    state_path = tmp_path / "import.json"
    original = _install(
        tmp_path, _snapshot(_session("s1", [("甲", "乙")], "角色甲"))
    )
    try:
        first, _ = sync_tavern_sessions(
            store, companion_id="fallback", state_path=state_path
        )
    finally:
        from app.plugins.manager import set_plugin_manager

        set_plugin_manager(original)

    assert first.turns == 1

    # 酒馆里又聊了两轮（同一个 jsonl 追加）
    original = _install(
        tmp_path,
        _snapshot(_session("s1", [("甲", "乙"), ("丙", "丁"), ("戊", "己")], "角色甲")),
    )
    try:
        second, _ = sync_tavern_sessions(
            store, companion_id="fallback", state_path=state_path
        )
    finally:
        from app.plugins.manager import set_plugin_manager

        set_plugin_manager(original)

    assert second.turns == 2                     # 只补新增的两轮
    assert TavernMemoryImporter(None, state_path=state_path).imported_turns(
        list(second.scopes)[0], "s1"
    ) == 3


def test_sync_scopes_sessions_by_character(tmp_path, store) -> None:
    """★ 同步也按角色分作用域（与手动导入同一条链路）。"""
    snapshot = _snapshot(
        _session("s1", [("甲", "乙")], "角色甲"),
        _session("s2", [("丙", "丁")], "角色乙"),
    )
    original = _install(tmp_path, snapshot)
    try:
        result, _ = sync_tavern_sessions(
            store, companion_id="fallback", state_path=tmp_path / "import.json"
        )
    finally:
        from app.plugins.manager import set_plugin_manager

        set_plugin_manager(original)

    assert len(result.scopes) == 2               # 两个角色 → 两个记忆作用域
    assert "fallback" not in result.scopes       # 全都匹配上了角色卡
    assert result.turns == 2


def test_sync_without_sessions_is_a_noop(tmp_path, store) -> None:
    original = _install(tmp_path, DataSourceSnapshot())
    try:
        result, warnings = sync_tavern_sessions(
            store, companion_id="fallback", state_path=tmp_path / "import.json"
        )
    finally:
        from app.plugins.manager import set_plugin_manager

        set_plugin_manager(original)

    assert result.turns == 0
    assert warnings == []
    assert not (tmp_path / "import.json").exists()   # 什么都没写就不该建文件
