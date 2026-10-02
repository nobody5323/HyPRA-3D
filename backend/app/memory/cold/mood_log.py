"""情绪日记存储（冷层扩展）。

用途：支撑 Agent 行动层的两个工具——
- `record_mood_journal`：记录一次情绪（情绪标签 + 强度 + 触发事件 + 备注）；
- `query_mood_trend`：统计近 N 天的情绪分布与平均强度。

与事实表（facts_*）同一数据库文件、同样按陪伴对象分表隔离（`mood_log_{companion}`），
便于后续做情绪趋势分析与可视化。
"""

import hashlib
import re
import sqlite3
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.paths import data_path


def default_db_path() -> Path:
    """情绪日记默认库文件位置（与冷层事实表共用，可写数据目录）。"""
    return data_path("data", "memory.db")


_TS_FMT = "%Y-%m-%dT%H:%M:%S.%f"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class MoodLogEntry:
    """一条情绪日记。"""

    companion_id: str
    emotion: str                    # 英文情绪标签（如 anxious）
    intensity: float = 0.5          # 0-1
    trigger: str = ""               # 触发事件（如「工作汇报」）
    note: str = ""                  # 补充备注
    session_id: str = ""
    entry_id: str = ""
    created_at: datetime = field(default_factory=datetime.now)


class MoodLogStore(ABC):
    """情绪日记存储抽象。"""

    @abstractmethod
    def add(self, entry: MoodLogEntry) -> str:
        """写入一条日记，返回 entry_id。"""

    @abstractmethod
    def list_recent(
        self, companion_id: str, *, days: int = 7, limit: int = 100
    ) -> list[MoodLogEntry]:
        """按时间倒序返回近 N 天的日记。"""

    @abstractmethod
    def trend(self, companion_id: str, *, days: int = 7) -> dict:
        """统计近 N 天情绪分布：{total, distribution, avg_intensity, dominant}。"""

    @abstractmethod
    def clear_scope(self, companion_id: str) -> int:
        """删除该陪伴对象的**全部**情绪日记（连表一起删），返回被删除的条数。

        没有该表时返回 0，不报错。不可恢复，调用方需二次确认。
        """


def _normalize(companion_id: str) -> str:
    """表名安全规范化（与冷层事实表策略一致）。"""
    slug = re.sub(r"[^0-9A-Za-z_]", "_", companion_id)
    if slug == companion_id:
        return slug
    digest = hashlib.md5(companion_id.encode("utf-8")).hexdigest()[:6]
    return f"{slug}_{digest}"


def _valid(companion_id: str) -> str:
    if not companion_id or not _SAFE_ID.match(companion_id):
        raise ValueError(f"非法的 companion_id：{companion_id!r}")
    return companion_id


def _table(companion_id: str) -> str:
    return f"mood_log_{_normalize(companion_id)}"


class SqliteMoodLogStore(MoodLogStore):
    """SQLite 实现（与冷层共用库文件）。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        # 缺省值在**调用时**求值：打包形态的数据目录要跟着环境变量走
        self._db_path = Path(db_path) if db_path is not None else default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self, conn: sqlite3.Connection, companion_id: str) -> None:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_table(companion_id)} (
                entry_id     TEXT PRIMARY KEY,
                emotion      TEXT NOT NULL,
                intensity    REAL NOT NULL DEFAULT 0.5,
                trigger      TEXT NOT NULL DEFAULT '',
                note         TEXT NOT NULL DEFAULT '',
                session_id   TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _ts(dt: datetime) -> str:
        return dt.strftime(_TS_FMT)

    @staticmethod
    def _parse(value: str) -> datetime:
        try:
            return datetime.strptime(value, _TS_FMT)
        except ValueError:
            return datetime.fromisoformat(value)

    def add(self, entry: MoodLogEntry) -> str:
        companion_id = _valid(entry.companion_id)
        entry_id = entry.entry_id or uuid.uuid4().hex
        with self._connect() as conn:
            self._ensure_table(conn, companion_id)
            conn.execute(
                f"""
                INSERT OR REPLACE INTO {_table(companion_id)}
                (entry_id, emotion, intensity, trigger, note, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    entry.emotion,
                    float(entry.intensity),
                    entry.trigger,
                    entry.note,
                    entry.session_id,
                    self._ts(entry.created_at),
                ),
            )
        return entry_id

    def list_recent(
        self, companion_id: str, *, days: int = 7, limit: int = 100
    ) -> list[MoodLogEntry]:
        companion_id = _valid(companion_id)
        since = self._ts(datetime.now() - timedelta(days=max(1, days)))
        with self._connect() as conn:
            self._ensure_table(conn, companion_id)
            rows = conn.execute(
                f"""
                SELECT * FROM {_table(companion_id)}
                WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?
                """,
                (since, limit),
            ).fetchall()
        return [
            MoodLogEntry(
                companion_id=companion_id,
                entry_id=row["entry_id"],
                emotion=row["emotion"],
                intensity=row["intensity"],
                trigger=row["trigger"],
                note=row["note"],
                session_id=row["session_id"],
                created_at=self._parse(row["created_at"]),
            )
            for row in rows
        ]

    def trend(self, companion_id: str, *, days: int = 7) -> dict:
        entries = self.list_recent(companion_id, days=days, limit=500)
        distribution: dict[str, int] = {}
        for entry in entries:
            distribution[entry.emotion] = distribution.get(entry.emotion, 0) + 1

        total = len(entries)
        avg_intensity = round(sum(e.intensity for e in entries) / total, 2) if total else 0.0
        dominant = max(distribution, key=lambda k: distribution[k]) if distribution else ""

        return {
            "days": days,
            "total": total,
            "distribution": distribution,
            "avg_intensity": avg_intensity,
            "dominant": dominant,
        }

    def clear_scope(self, companion_id: str) -> int:
        """删除该陪伴对象的情绪日记表，返回被删除的条数（同冷层事实表的做法）。"""
        companion_id = _valid(companion_id)
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
