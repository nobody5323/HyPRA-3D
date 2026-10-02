"""会话存储接口。

调用方（`app/api/chat.py`）只依赖本接口，具体存储按配置替换：

    memory → SessionRepository      进程内内存，重启即清空（测试 / 轻量场景）
    sqlite → SqliteSessionRepository 落 backend/data（**刷新页面与重启后端都不丢**）

接口收敛为：create / get / append_turn / set_state_var / list_history / list_sessions。
新增存储实现时只需满足这些语义，路由层无需改动。
"""

from abc import ABC, abstractmethod

from app.session.context import (
    DEFAULT_MAX_HISTORY_TURNS,
    ChatTurn,
    SessionContext,
    SessionSummary,
)
from app.session.mode import MODE_COMPANION


class SessionStore(ABC):
    """会话存储抽象。"""

    @abstractmethod
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
        """创建会话；session_id 缺省时自动生成，重复则抛 ValueError。

        `mode` 见 `app/session/mode.py`：会话级属性，创建后不再变（切模式 = 新会话）。
        """

    @abstractmethod
    def get(self, session_id: str) -> SessionContext | None:
        """按 id 取会话（含窗口内的历史）；不存在返回 None。"""

    @abstractmethod
    def append_turn(self, session_id: str, turn: ChatTurn) -> SessionContext:
        """追加一条消息（窗口裁剪由实现负责），返回更新后的会话。"""

    @abstractmethod
    def append_turns(self, session_id: str, turns: list[ChatTurn]) -> SessionContext:
        """一次性追加多条消息（**同一事务**）。

        一轮对话的 user + assistant 两条不应分两次写：进程在两次之间中断会
        在库里留下孤立的用户消息（界面有气泡、刷新后却不见回复）。
        """

    @abstractmethod
    def set_state_var(self, session_id: str, name: str, value: str) -> SessionContext:
        """更新状态变量**并落库**（路由层必须走这里，不能直接改 SessionContext）。"""

    @abstractmethod
    def list_history(self, session_id: str, limit: int | None = None) -> list[ChatTurn]:
        """返回历史消息（时间序）；limit 缺省时用会话的窗口大小。

        `limit <= 0` 返回空列表（两个实现必须对齐；否则内存实现的
        `archive[-0:]` 会返回全部，与 SQLite 的 `LIMIT 0` 结果相反）。
        """
    @abstractmethod
    def list_sessions(
        self, persona_id: str | None = None, limit: int = 50
    ) -> list[SessionSummary]:
        """列出会话（按最近更新倒序）；persona_id 为 None 时返回全部。"""

    @abstractmethod
    def delete_session(self, session_id: str) -> int:
        """删除会话及其全部消息，返回被删除的消息条数。

        会话不存在时抛 KeyError（路由层据此返回 404，而不是静默成功）。
        归属校验（不属于该陪伴对象不得删）由路由层负责。
        """

    @abstractmethod
    def delete_sessions(self, persona_id: str) -> tuple[int, int]:
        """删除某个陪伴对象的**全部**会话，返回 (会话数, 消息数)。

        为什么要有它：会话是「每轮对话都可能新建」的对象，试聊很容易积累出
        几十个同名会话（标题就是首条用户消息，看着一模一样）。一条条删不现实。

        该角色没有任何会话时返回 (0, 0)——不是错误：清空一个已经空的列表
        是幂等操作，调用方不需要先查一遍。
        """
