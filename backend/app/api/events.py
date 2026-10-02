"""`GET /events`：SSE 推送通道（前端接收主动消息的唯一入口）。

设计见 `docs/proactive-multimodal.md` §5.1。三条约定：

1. **作用域必填**：前端必须声明自己在看哪个陪伴对象。主动消息属于某个角色，
   不声明就只能靠猜——切换角色后收到另一个角色的话，用户无法解释。
2. **心跳必须发**：中间的代理（nginx / 公司网关）会在连接静默一段时间后掐断。
   只靠 `yield` 事件是不够的——用户可能几十分钟都没有主动消息。
3. **断开必须能被察觉**：SSE 是长连接，客户端关标签页时不会发任何请求。
   靠 `request.is_disconnected()` 与「发送失败」两条路径收尾，
   否则会留下永远不退订的队列（见 `EventBus.subscribe` 的说明）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.events.bus import Event, get_event_bus
from app.events.types import EVENT_PERCEPTION_HINT, event_scope

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

#: 心跳间隔（秒）。取 15s：远小于常见代理的 60s 空闲超时，
#: 又不至于让「一条消息都没有」时白跑太多帧。
HEARTBEAT_SECONDS = 15.0


def _scope_for(persona_id: str) -> str:
    """陪伴对象 → 事件作用域（口径见 `app/events/types.py: event_scope`）。"""
    return event_scope(persona_id)


@router.get("/events")
async def stream_events(
    request: Request,
    persona_id: str = Query(..., description="当前陪伴对象 id（决定订阅哪个作用域）"),
) -> StreamingResponse:
    """订阅主动消息（Server-Sent Events）。

    连接建立后会立刻收到一条 `perception_hint`（`kind="ready"`）——
    前端据此确认「通道通了」，而不是靠「等很久没消息」来推断，
    那种推断在「通道坏了」和「确实没有主动消息」之间无法区分。
    """
    scope = _scope_for(persona_id)
    bus = get_event_bus()

    async def event_stream():
        async with bus.subscribe(scope) as queue:
            # 就绪帧：让前端能把「连上了」和「没消息」分开
            ready = Event(
                type=EVENT_PERCEPTION_HINT,
                payload={"source": "events", "kind": "ready", "scope": scope},
            )
            yield ready.to_sse()

            while True:
                if await request.is_disconnected():
                    logger.debug("SSE 客户端断开：%s", scope)
                    return
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    # 注释帧（以 `:` 开头）：浏览器会忽略，但连接保持活跃
                    yield ": keep-alive\n\n"
                    continue
                yield event.to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 nginx 的响应缓冲：否则事件会被攒在代理里，主动消息「迟到」
            # 到失去意义（这也是 SSE 最常见的「本地好、部署后不动」的原因）
            "X-Accel-Buffering": "no",
        },
    )
