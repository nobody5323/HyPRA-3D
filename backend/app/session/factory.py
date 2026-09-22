"""会话存储工厂：按配置选择实现（与 memory / knowledge / llm 的 factory 风格一致）。"""

from pathlib import Path

from app.session.base import SessionStore
from app.session.repository import SessionRepository
from app.session.sqlite_repository import SqliteSessionRepository

#: 支持的会话存储后端
SUPPORTED_BACKENDS = ("memory", "sqlite")


def create_session_store(backend: str, db_path: str | Path = "") -> SessionStore:
    """按 backend 创建会话存储。

    参数:
        backend: "memory"（进程内存）| "sqlite"（落库，刷新/重启都不丢）；
        db_path: SQLite 库文件位置（memory 后端忽略）。
                 路由层通常传「session_db_path 或冷层 cold_db_path」——
                 同库不同表，评审只需配一处。
    """
    name = (backend or "").strip().lower()
    if name == "memory":
        return SessionRepository()
    if name == "sqlite":
        if not db_path:
            raise ValueError("sqlite 会话后端必须提供 db_path")
        return SqliteSessionRepository(db_path)
    raise ValueError(f"不支持的会话后端：{backend!r}（可选 {'/'.join(SUPPORTED_BACKENDS)}）")
