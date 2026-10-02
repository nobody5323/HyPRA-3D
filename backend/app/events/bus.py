"""进程内事件总线（SSE 的服务端一半）。

## 为什么必须有它

主动沟通的**物理前提**是服务端能推送。现状前端只有 `POST /chat` 请求-响应，
后端没有任何通道能把一条消息送进界面——触发器写得再漂亮也送不出去。
本模块就是那条通道的服务端出口（另一半是 `GET /events`）。

## 为什么是进程内 pub/sub，而不是 Redis / 消息队列

后端的两种形态（开发 `uvicorn`、打包 `backend.exe`）**都是单进程**，
进程内广播已经完整覆盖需求。引入外部中间件会凭空多一个部署依赖，
而收益为零——除非将来上多 worker。

⚠️ **单进程是硬前提**：多 worker 下每个 worker 有自己的一份 `EventBus`，
主动消息只会送达「恰好持有那条 SSE 连接」的那个 worker，表现为
**消息随机丢失**且极难定位。真要上多 worker，必须先把这里换成外部 pub/sub，
并且在那之前不能声称主动沟通可用。

## 为什么是 SSE 而不是 WebSocket

| 理由 | 说明 |
| --- | --- |
| 主动消息本就是**单向**（服务端 → 客户端） | 不需要双向握手 |
| 走 HTTP，**零新依赖** | FastAPI 的 `StreamingResponse` 直接够用 |
| 浏览器**自动重连** | 省掉一整套重连逻辑 |
| 反代 / 防火墙友好 | 与现有 docker compose 部署方式无冲突 |

## 队列满了怎么办：丢最旧，绝不阻塞发布方

发布方可能是对话路由（用户正等着回复）。如果 `put` 阻塞，一个卡住的
浏览器标签页就能把整条对话链路拖死——**这是不可接受的耦合**。
因此队列有界，满了就丢最旧的一条并记 debug 日志：实时推送丢一条，
比「后端因为某个客户端卡住而变慢」轻得多。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

#: 单个订阅者的队列深度。够覆盖「浏览器切标签页导致消费变慢」的短暂积压；
#: 再深只会让内存里堆着早已过期的主动消息。
DEFAULT_QUEUE_SIZE = 64


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Event:
    """一条待推送的事件。"""

    type: str
    payload: dict = field(default_factory=dict)
    at: str = field(default_factory=_now_iso)

    def to_sse(self) -> str:
        """渲染成 SSE 帧。

        `event:` 用类型名（前端 `addEventListener` 直接订阅）；
        `data:` 是 JSON（含 type，便于只监听 `message` 的通用处理器兜底）。
        """
        body = json.dumps(
            {"type": self.type, "at": self.at, **self.payload},
            ensure_ascii=False,
        )
        return f"event: {self.type}\ndata: {body}\n\n"


class EventBus:
    """按作用域（scope）分房间的进程内广播。

    scope 语义与记忆作用域一致（`companion:{persona_id}`）：主动消息属于**某个陪伴对象**，
    界面只应收到「当前选中的那个角色」的消息——否则切换角色后
    会收到另一个角色的话，且无法解释。
    """

    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self.queue_size = queue_size
        #: scope → 订阅者队列集合
        self._rooms: dict[str, set[asyncio.Queue[Event]]] = {}
        #: 事件循环引用：供**非事件循环线程**（如记忆写入的线程池）安全发布
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---------- 生命周期 ----------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定事件循环（lifespan 启动时调用）。

        绑定后才支持跨线程发布。不绑定也不会报错——只是跨线程发布会记 warning 并丢弃，
        因为「事件丢了」远好过「为了发一条通知去创建/猜测一个事件循环」。
        """
        self._loop = loop

    def unbind_loop(self) -> None:
        """解绑（关闭时调用），避免持有已关闭的循环引用。"""
        self._loop = None

    # ---------- 订阅 ----------

    @asynccontextmanager
    async def subscribe(self, scope: str) -> AsyncIterator[asyncio.Queue[Event]]:
        """订阅一个作用域，退出上下文时自动退订。

        用上下文管理器而不是「返回队列 + 手工退订」：SSE 连接断开的路径有好几条
        （客户端关闭、心跳失败、服务端取消），漏掉任何一条都会留下一个
        **永远没人消费的队列**，随后每次发布都在往它里面塞数据。
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self.queue_size)
        self._rooms.setdefault(scope, set()).add(queue)
        logger.debug("事件订阅：%s（当前 %d 个）", scope, len(self._rooms[scope]))
        try:
            yield queue
        finally:
            room = self._rooms.get(scope)
            if room is not None:
                room.discard(queue)
                if not room:
                    self._rooms.pop(scope, None)
            logger.debug("事件退订：%s", scope)

    # ---------- 发布 ----------

    def publish(self, scope: str, event: Event) -> int:
        """发布事件，返回投递到的订阅者数。

        可在**任意线程**调用：
        - 事件循环线程内直接投递（绝大多数情况：路由、调度任务）；
        - 其它线程（记忆写入的线程池）经 `call_soon_threadsafe` 转交——
          `asyncio.Queue` 不是线程安全的，直接 `put_nowait` 会偶发丢事件且无法复现。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._publish_from_thread(scope, event)
        return self._deliver(scope, event)

    def _publish_from_thread(self, scope: str, event: Event) -> int:
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning(
                "跨线程发布事件但事件循环不可用，已丢弃：%s/%s", scope, event.type
            )
            return 0
        loop.call_soon_threadsafe(self._deliver, scope, event)
        return -1  # 已排入循环，投递数在下一拍才知道

    def _deliver(self, scope: str, event: Event) -> int:
        """真正的投递（必须在事件循环线程执行）。"""
        room = self._rooms.get(scope)
        if not room:
            # 没有订阅者不是错误：桌宠窗没开、用户没打开界面时都会走到这里。
            # 主动消息「没人接收」是正常状态，不该刷 warning。
            logger.debug("事件无订阅者，已丢弃：%s/%s", scope, event.type)
            return 0

        delivered = 0
        for queue in list(room):
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                # 丢最旧的一条再放新的：实时推送里「最新的」才有价值
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                    delivered += 1
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover - 竞态兜底
                    logger.debug("事件队列异常，跳过该订阅者")
        return delivered

    # ---------- 观测 ----------

    def subscriber_count(self, scope: str | None = None) -> int:
        """订阅者数量（`/health` 与测试用）。"""
        if scope is not None:
            return len(self._rooms.get(scope, ()))
        return sum(len(room) for room in self._rooms.values())

    def scopes(self) -> list[str]:
        """当前有订阅者的作用域列表。"""
        return sorted(self._rooms)


#: 进程内单例
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """取事件总线单例。"""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def set_event_bus(bus: EventBus | None) -> None:
    """替换/重置事件总线（测试注入用）。"""
    global _bus
    _bus = bus
