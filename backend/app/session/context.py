"""对话会话上下文：单轮消息与会话状态。

历史窗口（喂给模型的最近 N 条）与**原始留档**是两件事：
- `SessionContext.history` 只承载窗口内的消息，用于装配提示词；
- 完整历史由存储层保留（SQLite 实现不裁剪），界面「历史记录」据此展示。

时间字段（created_at / updated_at）为可选：内存实现可填，纯逻辑测试可忽略。
"""

from dataclasses import dataclass, field


# 会话历史默认保留的消息条数（约 20 轮一往一返）
DEFAULT_MAX_HISTORY_TURNS = 20


@dataclass
class ChatTurn:
    """一条对话消息。"""

    role: str   # "user" | "assistant"
    text: str
    # ISO 8601 时间串（持久化实现填写；内存实现与纯逻辑测试可为 None）
    created_at: str | None = None


@dataclass
class SessionContext:
    """一次陪伴会话的运行时状态。"""

    session_id: str
    persona_id: str
    user_name: str = "朋友"
    state_vars: dict[str, str] = field(default_factory=dict)
    history: list[ChatTurn] = field(default_factory=list)
    max_history_turns: int = DEFAULT_MAX_HISTORY_TURNS
    created_at: str | None = None
    updated_at: str | None = None

    def append_turn(self, turn: ChatTurn) -> None:
        """追加一条消息，并自动裁剪超出窗口的旧消息（保留最近 N 条）。"""
        self.history.append(turn)
        if len(self.history) > self.max_history_turns:
            self.history = self.history[-self.max_history_turns:]

    def set_state_var(self, name: str, value: str) -> None:
        """更新一个动态状态变量（如 current_mood）。

        注意：**直接改这里不会落到持久层**。路由层请走
        `SessionStore.set_state_var()`，否则 SQLite 实现下重启即丢。
        """
        self.state_vars[name] = value


@dataclass
class SessionSummary:
    """会话列表项（界面「历史记录」用）。"""

    session_id: str
    persona_id: str
    # 首条用户消息的前若干字（无用户消息时为「新对话」）
    title: str
    message_count: int
    # ISO 8601（用于排序与展示）
    updated_at: str


#: 会话标题的最大字数
TITLE_MAX_CHARS = 24


def build_session_title(first_user_text: str | None) -> str:
    """由首条用户消息生成会话标题（两个存储实现共用，避免展示规则漂移）。"""
    text = " ".join((first_user_text or "").split())
    if not text:
        return "新对话"
    if len(text) <= TITLE_MAX_CHARS:
        return text
    return text[:TITLE_MAX_CHARS] + "…"
