"""冷层 SQLite 实现。

存储文件：backend/data/memory.db（已在 gitignore 排除；路径可经构造参数覆盖）。
隔离：按 companion_id 分表 —— facts_{companion}（参照⑧）。
安全：companion_id 先做白名单校验，再规范化为合法表名（连字符等 → 下划线），
      防止表名注入与 SQL 语法错误。
"""

import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from app.memory.cold.models import (
    Fact,
    FactStatus,
    FactType,
)
from app.memory.cold.store import ColdMemoryStore
from app.memory.naming import normalize_scope_id
from app.paths import data_path


def default_db_path() -> Path:
    """冷层默认库文件位置（可写数据目录）。"""
    return data_path("data", "memory.db")


# 日期时间格式（与 datetime.fromisoformat 兼容的精简格式）
_TS_FMT = "%Y-%m-%dT%H:%M:%S.%f"


# 允许的 companion_id 字符集（字母数字、下划线、连字符）
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _table(companion_id: str) -> str:
    """按陪伴对象生成事实表名（非法字符经 normalize_scope_id 规范化）。"""
    return f"facts_{normalize_scope_id(companion_id)}"


def _valid_companion_id(companion_id: str) -> str:
    """companion_id 白名单校验（防表名注入）。"""
    if not companion_id or not _SAFE_ID.match(companion_id):
        raise ValueError(f"非法的 companion_id：{companion_id!r}")
    return companion_id


class SqliteColdStore(ColdMemoryStore):
    """基于 SQLite 的冷层存储。非线程安全的连接由每次操作新建保证。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        # 缺省值在**调用时**求值：打包形态的数据目录要跟着环境变量走
        self._db_path = Path(db_path) if db_path is not None else default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- 内部工具 ----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self, conn: sqlite3.Connection, companion_id: str) -> None:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_table(companion_id)} (
                fact_id       TEXT PRIMARY KEY,
                type          TEXT NOT NULL,
                subject       TEXT NOT NULL,
                predicate     TEXT NOT NULL,
                object        TEXT NOT NULL,
                detail        TEXT NOT NULL DEFAULT '',
                occurred_at   TEXT,
                created_at    TEXT NOT NULL,
                last_seen_at  TEXT,
                importance    INTEGER NOT NULL DEFAULT 3,
                confidence    REAL NOT NULL DEFAULT 0.8,
                status        TEXT NOT NULL DEFAULT 'active',
                emotion_tag   TEXT,
                keywords      TEXT NOT NULL DEFAULT '[]',
                source        TEXT NOT NULL DEFAULT ''
            )
            """
        )

    @staticmethod
    def _ts(dt: datetime) -> str:
        return dt.strftime(_TS_FMT)

    @staticmethod
    def _parse_ts(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.strptime(value, _TS_FMT)

    def _row_to_fact(self, row: sqlite3.Row) -> Fact:
        import json

        return Fact(
            fact_id=row["fact_id"],
            type=FactType(row["type"]),
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object"],
            detail=row["detail"],
            occurred_at=row["occurred_at"],
            created_at=self._parse_ts(row["created_at"]),
            last_seen_at=self._parse_ts(row["last_seen_at"]),
            importance=row["importance"],
            confidence=row["confidence"],
            status=FactStatus(row["status"]),
            emotion_tag=row["emotion_tag"],
            keywords=json.loads(row["keywords"]),
            source=row["source"],
        )

    # ---------- 事实操作 ----------

    def save_fact(self, companion_id: str, fact: Fact) -> str:
        companion_id = _valid_companion_id(companion_id)
        fact_id = fact.fact_id or uuid.uuid4().hex
        import json

        with self._connect() as conn:
            self._ensure_table(conn, companion_id)
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {_table(companion_id)} (
                    fact_id, type, subject, predicate, object, detail,
                    occurred_at, created_at, last_seen_at, importance,
                    confidence, status, emotion_tag, keywords, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact_id,
                    fact.type.value,
                    fact.subject,
                    fact.predicate,
                    fact.object,
                    fact.detail,
                    fact.occurred_at,
                    self._ts(fact.created_at),
                    self._ts(fact.last_seen_at) if fact.last_seen_at else None,
                    fact.importance,
                    fact.confidence,
                    fact.status.value,
                    fact.emotion_tag,
                    json.dumps(fact.keywords, ensure_ascii=False),
                    fact.source,
                ),
            )
        return fact_id

    def list_facts(
        self,
        companion_id: str,
        *,
        type: FactType | None = None,
        status: FactStatus = FactStatus.ACTIVE,
        limit: int = 50,
    ) -> list[Fact]:
        companion_id = _valid_companion_id(companion_id)
        query = f"SELECT * FROM {_table(companion_id)} WHERE status = ?"
        params: list = [status.value]
        if type is not None:
            query += " AND type = ?"
            params.append(type.value)
        # 排序键必须在 SQL 内生效：若先按 created_at 截断，高 importance 但创建
        # 较早的事实会被永久排除在召回之外，Python 侧再排序无法补救。
        query += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            self._ensure_table(conn, companion_id)
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_fact(r) for r in rows]

    def get_fact(self, companion_id: str, fact_id: str) -> Fact | None:
        companion_id = _valid_companion_id(companion_id)
        with self._connect() as conn:
            self._ensure_table(conn, companion_id)
            row = conn.execute(
                f"SELECT * FROM {_table(companion_id)} WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
        return self._row_to_fact(row) if row else None

    def update_status(
        self,
        companion_id: str,
        fact_id: str,
        status: FactStatus,
    ) -> bool:
        companion_id = _valid_companion_id(companion_id)
        with self._connect() as conn:
            self._ensure_table(conn, companion_id)
            cur = conn.execute(
                f"UPDATE {_table(companion_id)} SET status = ? WHERE fact_id = ?",
                (status.value, fact_id),
            )
            affected = cur.rowcount
        return affected > 0

    def touch(self, companion_id: str, fact_id: str) -> bool:
        """印证事实：刷新 last_seen_at 并提升 confidence（上限 1.0）。

        「被反复提到的事更可信」——这提供了 importance 之外的第二个权重维度，
        用于弥补规则抽取器 importance 按类型取常量带来的失真。
        """
        companion_id = _valid_companion_id(companion_id)
        with self._connect() as conn:
            self._ensure_table(conn, companion_id)
            cur = conn.execute(
                f"""UPDATE {_table(companion_id)}
                    SET last_seen_at = ?, confidence = MIN(1.0, confidence + 0.1)
                    WHERE fact_id = ?""",
                (self._ts(datetime.now()), fact_id),
            )
            affected = cur.rowcount
        return affected > 0

    def delete_fact(self, companion_id: str, fact_id: str) -> bool:
        companion_id = _valid_companion_id(companion_id)
        with self._connect() as conn:
            self._ensure_table(conn, companion_id)
            cur = conn.execute(
                f"DELETE FROM {_table(companion_id)} WHERE fact_id = ?",
                (fact_id,),
            )
            affected = cur.rowcount
        return affected > 0

    def clear_scope(self, companion_id: str) -> int:
        """删除该陪伴对象的事实表，返回被删除的条数。

        用 DROP TABLE 而不是 `DELETE FROM`：表名本身就是按作用域命名的，
        整表丢掉更彻底（也顺手丢掉可能过时的列定义），下次写入时
        `_ensure_table` 会按当前 schema 重建。

        先查 sqlite_master 再动手：`DROP TABLE IF EXISTS` 不返回影响行数，
        而「到底删了几条」是要显示给用户的。
        """
        companion_id = _valid_companion_id(companion_id)
        table = _table(companion_id)
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if exists is None:
                return 0
            removed = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            conn.execute(f"DROP TABLE {table}")
        return int(removed or 0)
