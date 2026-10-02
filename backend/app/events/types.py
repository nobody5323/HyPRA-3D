"""事件类型常量（前后端契约，改这里必须同步前端 `lib/api/events.ts`）。

为什么用**常量而不是枚举**：它们要原样出现在 SSE 的 `event:` 字段里，
前端用 `addEventListener(字符串, ...)` 订阅——枚举的 `.value` 在序列化时
多一层转换，而这个字段是跨语言的字符串契约，越直接越好。
"""

#: 主动开口：payload 与 `POST /chat` 的响应**同构**（reply / emotion / speak …），
#: 前端因此可以复用同一套渲染路径，不必为主动消息写第二套。
EVENT_PROACTIVE_MESSAGE = "proactive_message"

#: 被节制层拦下：{trigger, reason}。**可观测性专用**——调参（冷却多久、配额多少）
#: 靠它，而不是靠猜「为什么它不说话」。
EVENT_PROACTIVE_SKIPPED = "proactive_skipped"

#: 感知状态提示（如「正在听歌」徽标）：{source, kind, summary}
EVENT_PERCEPTION_HINT = "perception_hint"


def event_scope(persona_id: str) -> str:
    """陪伴对象 id → 事件作用域键。

    形如 `companion:therapist-elder-sister`，与 `AGENTS.md §8.2` 的记忆作用域
    同一套写法。**前缀不能省**：将来会有 `session:{id}` 这类作用域，
    去掉前缀就再也分不出「这条消息属于哪个角色」还是「属于哪段会话」。

    与记忆作用域的唯一区别是这里**不做 slug 化**：事件只在进程内做字典键，
    不过 Qdrant / SQLite，没有字符限制；做 slug 反而会让「前端传了什么」
    和「后端拿什么查」对不上，增加排查成本。
    """
    return f"companion:{(persona_id or '').strip()}"
