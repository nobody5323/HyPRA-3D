"""会话存储：SQLite 实现（**刷新页面 / 重启后端都不丢**）。

存储位置默认与冷层同一库文件（`backend/data/memory.db`，已在 gitignore），
表名独立、不复用记忆表：

    chat_sessions  会话元信息（人设、模式、称呼、状态变量、窗口大小、时间）
    chat_turns     逐条消息 —— **原始留档，不裁剪**

「窗口裁剪」与「原始留档」分开是刻意的：
- 喂给模型的上下文只取窗口内的最近 N 条（`max_history_turns`，读取时 LIMIT）；
- 数据库保留全部消息，界面「历史记录」因此能看到完整对话，
  而不是跟着窗口一起丢。

隔离：会话按 `persona_id`（陪伴对象）划分，切换陪伴对象不会串会话。
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.session.base import SessionStore
from app.session.context import (
    DEFAULT_MAX_HISTORY_TURNS,
    ChatTurn,
    SessionContext,
    SessionSummary,
    build_session_title,
)
from app.session.mode import MODE_COMPANION, normalize_mode

# 与冷层一致的紧凑时间格式（datetime.fromisoformat 可直接解析）
_TS_FMT = "%Y-%m-%dT%H:%M:%S.%f"

# 遇锁时的等待上限（秒）：同一库文件还有冷层（事实）与情绪日志在写
_BUSY_TIMEOUT_S = 5.0

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        session_id        TEXT PRIMARY KEY,
        persona_id        TEXT NOT NULL,
        mode              TEXT NOT NULL DEFAULT 'companion',
        user_name         TEXT NOT NULL DEFAULT '朋友',
        state_vars        TEXT NOT NULL DEFAULT '{}',
        max_history_turns INTEGER NOT NULL DEFAULT 20,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_turns (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role       TEXT NOT NULL,
        text       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON chat_turns(session_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_chat_sessions_persona ON chat_sessions(persona_id, updated_at)",
)

#: 存量库的增量迁移，每项 `(表, 列, 列定义)`。
#:
#: `CREATE TABLE IF NOT EXISTS` **不会**给已存在的老表补列，而 `data/memory.db`
#: 是长期留存的（用户的会话不能因为加个字段就丢），所以新增列必须在这里补一次。
#: 老会话补上默认值 `companion` 是对的：加 `mode` 之前的行为就是桌宠对话模式。
_MIGRATIONS = (
    ("chat_sessions", "mode", "TEXT NOT NULL DEFAULT 'companion'"),
)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """给存量表补上缺失的列（幂等：已存在则跳过）。"""
    for table, column, ddl in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _now() -> str:
    return datetime.now().strftime(_TS_FMT)


class SqliteSessionRepository(SessionStore):
    """基于 SQLite 的会话存储。连接每次操作新建（非线程安全的连接不共享）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        # 建表只在本实例的首个连接上执行（DDL 是幂等的，但没必要每次操作都跑 5 条）
        self._schema_ready = False

    # ---------- 内部工具 ----------

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """打开连接并保证建表、提交、关闭。

        - `journal_mode=WAL`：读不被写阻塞（同一库文件还有冷层/情绪日志在写）；
        - `busy_timeout`：遇到锁等待而不是立刻抛 `database is locked`（连接参数
          的 timeout 只影响建立连接阶段）。
        写入失败仍是「整事务回滚」——不会写脏既有数据。
        """
        conn = sqlite3.connect(self._db_path, timeout=_BUSY_TIMEOUT_S)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_S * 1000)}")
            if not self._schema_ready:
                for statement in _SCHEMA:
                    conn.execute(statement)
                _apply_migrations(conn)
                self._schema_ready = True
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _row_to_session(row: sqlite3.Row, history: list[ChatTurn]) -> SessionContext:
        return SessionContext(
            session_id=row["session_id"],
            persona_id=row["persona_id"],
            # 迁移保证列一定存在；仍过一道 normalize，手改过的值不让它炸
            mode=normalize_mode(row["mode"]),
            user_name=row["user_name"],
            state_vars=json.loads(row["state_vars"] or "{}"),
            history=history,
            max_history_turns=int(row["max_history_turns"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _fetch_turns(
        conn: sqlite3.Connection, session_id: str, limit: int
    ) -> list[ChatTurn]:
        """取最近 limit 条（按时间序返回）。"""
        rows = conn.execute(
            "SELECT role, text, created_at FROM chat_turns"
            " WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            ChatTurn(role=row["role"], text=row["text"], created_at=row["created_at"])
            for row in reversed(rows)
        ]

    @staticmethod
    def _require_row(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"会话不存在：{session_id}")
        return row

    # ---------- 接口实现 ----------

    def create(
        self,
        persona_id: str,
        *,
        mode: str = MODE_COMPANION,
        user_name: str = "朋友",
        state_vars: dict[str, str] | None = None,
        session_id: str | None = None,
        max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS,
    ) -> SessionContext:
        sid = session_id or uuid.uuid4().hex
        now = _now()
        with self._session() as conn:
            try:
                conn.execute(
                    "INSERT INTO chat_sessions"
                    " (session_id, persona_id, mode, user_name, state_vars,"
                    "  max_history_turns, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sid,
                        persona_id,
                        normalize_mode(mode),
                        user_name,
                        json.dumps(dict(state_vars or {}), ensure_ascii=False),
                        max_history_turns,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"会话已存在：{sid}") from exc
            row = self._require_row(conn, sid)
        return self._row_to_session(row, [])

    def get(self, session_id: str) -> SessionContext | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            history = self._fetch_turns(conn, session_id, int(row["max_history_turns"]))
        return self._row_to_session(row, history)

    def append_turn(self, session_id: str, turn: ChatTurn) -> SessionContext:
        """追加一条消息（单条写入；一轮对话请用 append_turns 保证原子性）。"""
        return self.append_turns(session_id, [turn])

    def append_turns(self, session_id: str, turns: list[ChatTurn]) -> SessionContext:
        """一次性追加多条消息（**同一事务**）。

        一轮对话的 user + assistant 必须一起落库：分两次写时若中间失败，
        库里会留下孤立的用户消息（界面有气泡、刷新后却没有回复）。
        """
        with self._session() as conn:
            self._require_row(conn, session_id)
            if turns:
                now = _now()
                conn.executemany(
                    "INSERT INTO chat_turns (session_id, role, text, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    [(session_id, turn.role, turn.text, turn.created_at or now) for turn in turns],
                )
                conn.execute(
                    "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
                    (now, session_id),
                )
        session = self.get(session_id)
        if session is None:  # pragma: no cover - 刚写入即不存在属异常
            raise KeyError(f"会话不存在：{session_id}")
        return session

    def set_state_var(self, session_id: str, name: str, value: str) -> SessionContext:
        with self._session() as conn:
            row = self._require_row(conn, session_id)
            state_vars = json.loads(row["state_vars"] or "{}")
            state_vars[name] = value
            conn.execute(
                "UPDATE chat_sessions SET state_vars = ?, updated_at = ?"
                " WHERE session_id = ?",
                (json.dumps(state_vars, ensure_ascii=False), _now(), session_id),
            )
        session = self.get(session_id)
        if session is None:  # pragma: no cover
            raise KeyError(f"会话不存在：{session_id}")
        return session

    def list_history(self, session_id: str, limit: int | None = None) -> list[ChatTurn]:
        with self._session() as conn:
            row = self._require_row(conn, session_id)
            effective = int(row["max_history_turns"]) if limit is None else limit
            # limit<=0 → 空（内存实现已对齐；SQLite 的 LIMIT -1 会返回全部）
            if effective <= 0:
                return []
            return self._fetch_turns(conn, session_id, effective)

    def list_sessions(
        self, persona_id: str | None = None, limit: int = 50
    ) -> list[SessionSummary]:
        # 标题取首条用户消息（与内存实现共用 build_session_title，展示规则不漂移）
        sql = """
            SELECT s.session_id, s.persona_id, s.updated_at,
                   (SELECT COUNT(*) FROM chat_turns t
                     WHERE t.session_id = s.session_id) AS message_count,
                   (SELECT t2.text FROM chat_turns t2
                     WHERE t2.session_id = s.session_id AND t2.role = 'user'
                     ORDER BY t2.id LIMIT 1) AS first_user_text
              FROM chat_sessions s
             WHERE (? IS NULL OR s.persona_id = ?)
             ORDER BY s.updated_at DESC
             LIMIT ?
        """
        with self._session() as conn:
            rows = conn.execute(sql, (persona_id, persona_id, limit)).fetchall()
        return [
            SessionSummary(
                session_id=row["session_id"],
                persona_id=row["persona_id"],
                title=build_session_title(row["first_user_text"]),
                message_count=int(row["message_count"] or 0),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def delete_session(self, session_id: str) -> int:
        """删除会话及其全部消息（**一个事务**），返回被删除的消息条数。"""
        with self._session() as conn:
            self._require_row(conn, session_id)
            cursor = conn.execute(
                "DELETE FROM chat_turns WHERE session_id = ?", (session_id,)
            )
            removed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
        return removed

    def delete_sessions(self, persona_id: str) -> tuple[int, int]:
        """删除某个陪伴对象的全部会话（**一个事务**），返回 (会话数, 消息数)。

        先查出该角色的全部 session_id，再分两条 IN 语句删消息与会话：
        顺着 `persona_id` 做子查询也能写，但显示改写时容易漏改一处，
        把「删某角色」变成「删全部」。
        """
        with self._session() as conn:
            rows = conn.execute(
                "SELECT session_id FROM chat_sessions WHERE persona_id = ?", (persona_id,)
            ).fetchall()
            session_ids = [row["session_id"] for row in rows]
            if not session_ids:
                return 0, 0

            placeholders = ",".join("?" * len(session_ids))
            cursor = conn.execute(
                f"DELETE FROM chat_turns WHERE session_id IN ({placeholders})", session_ids
            )
            removed_turns = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            conn.execute(
                f"DELETE FROM chat_sessions WHERE session_id IN ({placeholders})", session_ids
            )
        return len(session_ids), removed_turns
