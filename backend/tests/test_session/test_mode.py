"""交互模式（`app/session/mode.py`）与它在会话上的持久化。

模式的**行为落点**是知识检索是否纳入酒馆来源作用域（见 test_memory/knowledge），
这里只守两件事：模式怎么定出来、以及它有没有真的落在会话上。
"""

import sqlite3

import pytest

from app.session.mode import (
    MODE_COMPANION,
    MODE_TAVERN,
    normalize_mode,
    resolve_mode,
)
from app.session.repository import SessionRepository
from app.session.sqlite_repository import SqliteSessionRepository


# =============================================================
# 解析规则
# =============================================================


def test_default_is_companion() -> None:
    assert normalize_mode(None) == MODE_COMPANION
    assert resolve_mode() == MODE_COMPANION


def test_unknown_mode_falls_back_instead_of_raising() -> None:
    """手改坏的模式不该让用户连话都发不出去——回落到默认（功能最全的那个）。"""
    assert normalize_mode("TAVERN") == MODE_TAVERN          # 大小写宽容
    assert normalize_mode("酒馆") == MODE_COMPANION          # 未知值回落
    assert normalize_mode("  ") == MODE_COMPANION
    assert resolve_mode(explicit="nonsense") == MODE_COMPANION


def test_explicit_mode_wins() -> None:
    assert (
        resolve_mode(explicit=MODE_TAVERN, session_mode=MODE_COMPANION, st_preset_id="")
        == MODE_TAVERN
    )


def test_st_preset_selects_tavern_mode() -> None:
    """★ 选了 ST 预设即酒馆模式——它必须与组装路径（按 st_preset 分派）一致。"""
    assert resolve_mode(st_preset_id="beta-1-0") == MODE_TAVERN


def test_st_preset_outranks_session_mode() -> None:
    """★ 预设压过会话既有值，否则会出现「提示词走酒馆预设、记忆却按桌宠隔离」的错配。

    那种错配的表现正是「明明选了酒馆预设，酒馆世界书还是召不回来」。
    """
    assert (
        resolve_mode(session_mode=MODE_COMPANION, st_preset_id="beta-1-0") == MODE_TAVERN
    )


def test_session_mode_used_when_no_preset_selected() -> None:
    """显式建成 tavern 的会话，在没选预设时继续按 tavern 处理。"""
    assert resolve_mode(session_mode=MODE_TAVERN, st_preset_id="") == MODE_TAVERN


# =============================================================
# 会话持久化
# =============================================================


@pytest.mark.parametrize(
    "factory", [SessionRepository, None], ids=["memory", "sqlite"]
)
def test_mode_is_persisted_on_the_session(factory, tmp_path) -> None:
    store = factory() if factory is not None else SqliteSessionRepository(tmp_path / "s.db")

    created = store.create("persona-a", mode=MODE_TAVERN)

    assert created.mode == MODE_TAVERN
    assert store.get(created.session_id).mode == MODE_TAVERN


def test_mode_defaults_to_companion(tmp_path) -> None:
    store = SqliteSessionRepository(tmp_path / "s.db")

    assert store.create("persona-a").mode == MODE_COMPANION


def test_invalid_mode_is_normalized_on_write(tmp_path) -> None:
    store = SqliteSessionRepository(tmp_path / "s.db")

    assert store.create("persona-a", mode="乱写的").mode == MODE_COMPANION


def test_mode_survives_new_instance(tmp_path) -> None:
    """换实例（等价于重启后端）后模式还在——它是会话字段，不是进程内状态。"""
    db = tmp_path / "s.db"
    created = SqliteSessionRepository(db).create("persona-a", mode=MODE_TAVERN)

    reopened = SqliteSessionRepository(db).get(created.session_id)

    assert reopened is not None and reopened.mode == MODE_TAVERN


def test_legacy_database_without_mode_column_is_migrated(tmp_path) -> None:
    """★ 存量库迁移：老表没有 `mode` 列，打开时要补上且不丢数据。

    `CREATE TABLE IF NOT EXISTS` 不会给已存在的老表补列，而 `data/memory.db`
    是长期留存的——不补列的话老用户一升级就 `no such column: mode`。
    老会话补默认值 `companion` 也是对的：加 mode 之前的行为就是桌宠对话。
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE chat_sessions ("
        "  session_id TEXT PRIMARY KEY, persona_id TEXT NOT NULL,"
        "  user_name TEXT NOT NULL DEFAULT '朋友',"
        "  state_vars TEXT NOT NULL DEFAULT '{}',"
        "  max_history_turns INTEGER NOT NULL DEFAULT 20,"
        "  created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE chat_turns ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,"
        "  role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO chat_sessions VALUES"
        " ('old', 'persona-a', '朋友', '{}', 20, '2026-01-01T00:00:00.000000',"
        "  '2026-01-01T00:00:00.000000')"
    )
    conn.commit()
    conn.close()

    store = SqliteSessionRepository(db)
    legacy = store.get("old")

    assert legacy is not None
    assert legacy.persona_id == "persona-a"
    assert legacy.mode == MODE_COMPANION
    # 迁移后新写入照常
    assert store.create("persona-b", mode=MODE_TAVERN).mode == MODE_TAVERN
