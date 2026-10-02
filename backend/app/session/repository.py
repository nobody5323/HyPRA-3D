"""会话存储（进程内内存版）。

用途：单元测试与不需要持久化的轻量场景。
**重启即清空**，因此生产/演示默认用 SqliteSessionRepository（见 factory.py）。
"""

import uuid
from datetime import datetime

from app.session.base import SessionStore
from app.session.context import (
    DEFAULT_MAX_HISTORY_TURNS,
    ChatTurn,
    SessionContext,
    SessionSummary,
    build_session_title,
)
from app.session.mode import MODE_COMPANION, normalize_mode

_TS_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _now() -> str:
    return datetime.now().strftime(_TS_FMT)


class SessionRepository(SessionStore):
    """内存会话注册表（单进程可用，重启即清空）。

    语义与 SqliteSessionRepository **对齐**：
    - `SessionContext.history` 只保留窗口内的消息（喂给模型的上下文）；
    - `_archive` 保留全部消息（界面「历史记录」用）。
    两者分开才能在两种存储间切换时不出现「历史突然变短」。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionContext] = {}
        #: 全量消息留档（与 SQLite 实现的 chat_turns 表同语义）
        self._archive: dict[str, list[ChatTurn]] = {}

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
        """创建新会话；session_id 缺省时自动生成。"""
        sid = session_id or uuid.uuid4().hex
        if sid in self._sessions:
            raise ValueError(f"会话已存在：{sid}")
        now = _now()
        session = SessionContext(
            session_id=sid,
            persona_id=persona_id,
            mode=normalize_mode(mode),
            user_name=user_name,
            state_vars=dict(state_vars or {}),
            max_history_turns=max_history_turns,
            created_at=now,
            updated_at=now,
        )
        self._sessions[sid] = session
        self._archive[sid] = []
        return session

    def get(self, session_id: str) -> SessionContext | None:
        """按 id 取会话，不存在返回 None。"""
        return self._sessions.get(session_id)

    def append_turn(self, session_id: str, turn: ChatTurn) -> SessionContext:
        """向会话追加一条消息（窗口裁剪），返回更新后的会话。

        窗口内（模型上下文）与全量留档同时更新，与 SQLite 实现一致。
        """
        return self.append_turns(session_id, [turn])

    def append_turns(self, session_id: str, turns: list[ChatTurn]) -> SessionContext:
        """一次性追加多条消息（同一轮对话的 user + assistant）。"""
        session = self._require(session_id)
        for turn in turns:
            session.append_turn(turn)
            self._archive.setdefault(session_id, []).append(turn)
        if turns:
            session.updated_at = _now()
        return session

    def set_state_var(self, session_id: str, name: str, value: str) -> SessionContext:
        """更新状态变量（内存实现天然「已持久化」）。"""
        session = self._require(session_id)
        session.set_state_var(name, value)
        session.updated_at = _now()
        return session

    def list_history(self, session_id: str, limit: int | None = None) -> list[ChatTurn]:
        """返回历史消息（时间序，副本）；limit 缺省时用会话的窗口大小。"""
        session = self._require(session_id)
        archive = self._archive.get(session_id, [])
        effective = session.max_history_turns if limit is None else limit
        # limit<=0 → 空（与 SQLite 的 LIMIT 0 对齐；不要写成 archive[-0:]）
        if effective <= 0:
            return []
        return list(archive[-effective:])

    def list_sessions(
        self, persona_id: str | None = None, limit: int = 50
    ) -> list[SessionSummary]:
        """列出会话（按最近更新倒序）。"""
        items = [
            session
            for session in self._sessions.values()
            if persona_id is None or session.persona_id == persona_id
        ]
        items.sort(key=lambda session: session.updated_at or "", reverse=True)
        return [
            SessionSummary(
                session_id=session.session_id,
                persona_id=session.persona_id,
                title=build_session_title(
                    next(
                        (
                            turn.text
                            for turn in self._archive.get(session.session_id, [])
                            if turn.role == "user"
                        ),
                        None,
                    )
                ),
                message_count=len(self._archive.get(session.session_id, [])),
                updated_at=session.updated_at or "",
            )
            for session in items[:limit]
        ]

    def delete_session(self, session_id: str) -> int:
        """删除会话及其消息留档，返回被删除的消息条数。"""
        self._require(session_id)
        removed = len(self._archive.pop(session_id, []))
        del self._sessions[session_id]
        return removed

    def delete_sessions(self, persona_id: str) -> tuple[int, int]:
        """删除某个陪伴对象的全部会话，返回 (会话数, 消息数)。"""
        targets = [
            session_id
            for session_id, session in self._sessions.items()
            if session.persona_id == persona_id
        ]
        removed_turns = sum(len(self._archive.pop(session_id, [])) for session_id in targets)
        for session_id in targets:
            del self._sessions[session_id]
        return len(targets), removed_turns

    def _require(self, session_id: str) -> SessionContext:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"会话不存在：{session_id}")
        return session
