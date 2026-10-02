"""SSE 推送通道的 HTTP 契约测试（`GET /events`）。

`test_events/test_bus.py` 验的是总线本身；这里验**接口层**的四件事，
它们只有在真的走一遍 ASGI 请求时才暴露：响应头、就绪帧、作用域隔离、
以及流断开后订阅是否被释放。

## ⚠️ 为什么在 ASGI 层驱动，而不是用 TestClient

**SSE 是永不结束的流，而两种常规测试客户端都会把它缓冲完才返回**：

| 客户端 | 行为 | 结果 |
| --- | --- | --- |
| `httpx.ASGITransport` | `await self.app(...)` 收完 body 才构造 `Response` | **挂死** |
| `starlette.testclient.TestClient`（1.6） | `portal.call(self.app, scope, receive, send)` 同样等 app 返回 | **挂死** |

实测两者都会让测试卡在第二条用例上（不是报错，是永远不返回）。
所以这里直接以 ASGI 协议驱动应用：自己写 `receive` / `send`，
拿到需要的帧就 `cancel` 掉任务——那也正是真实客户端关掉标签页时发生的事，
顺带把「断开后退订」这条路径一起验了。
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.events.bus import Event, get_event_bus
from app.events.types import EVENT_PERCEPTION_HINT, EVENT_PROACTIVE_MESSAGE, event_scope
from app.main import app
from app.proactive.runner import ProactiveRunner
from app.proactive.state import get_state_store

PERSONA = "therapist-elder-sister"

#: 单条用例等待帧的上限（秒）。够宽松以容忍 CI 抖动，又能让「挂住」快速失败
FRAME_TIMEOUT = 10.0


def _scope(persona_id: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/events",
        "raw_path": b"/events",
        "query_string": f"persona_id={persona_id}".encode(),
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "state": {},
    }


class _SseSession:
    """一条被手工驱动的 SSE 连接（用完必须 `aclose`）。"""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.frames: list[dict] = []
        self._buffer = ""
        self._new_frame = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def open(self, persona_id: str) -> None:
        async def receive() -> dict:
            # 请求体已读完；之后**永远不返回**（模拟客户端保持连接）。
            # `Request.is_disconnected()` 内部用立即 cancel 的 CancelScope 包住
            # 这次 await，因此不会真的挂住。
            await asyncio.Event().wait()
            return {}  # pragma: no cover - 上面永不返回

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.headers = {
                    key.decode(): value.decode() for key, value in message.get("headers", [])
                }
                return
            if message["type"] == "http.response.body":
                self._buffer += message.get("body", b"").decode("utf-8")
                self._drain()

        self._task = asyncio.create_task(app(_scope(persona_id), receive, send))
        # 等到至少一帧（就绪帧）到达，否则调用方会读到空
        await self.wait_frames(1)

    def _drain(self) -> None:
        while "\n\n" in self._buffer:
            block, _, self._buffer = self._buffer.partition("\n\n")
            data_line = next(
                (line for line in block.splitlines() if line.startswith("data: ")), ""
            )
            if data_line:
                self.frames.append(json.loads(data_line.removeprefix("data: ")))
                self._new_frame.set()

    async def wait_frames(self, count: int) -> None:
        async def _until() -> None:
            while len(self.frames) < count:
                self._new_frame.clear()
                await self._new_frame.wait()

        await asyncio.wait_for(_until(), timeout=FRAME_TIMEOUT)

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 取消即可
                pass
        self._task = None


# ---------- 非流式接口（用 TestClient 更省事）----------


def test_events_requires_persona_id() -> None:
    """不声明在看哪个角色就只能靠猜——猜错的表现是「收到别人的消息」。"""
    with TestClient(app) as client:
        assert client.get("/events").status_code == 422


def test_proactive_run_skips_without_subscriber_over_http() -> None:
    """没人监听时不生成：事件会被总线丢弃，生成纯属浪费一次 LLM 调用。

    这条同时验证了「没订阅者」这条前置检查在**接口层**也成立——
    它比闸门更早，因此被它拦下时不消耗任何模型调用。
    """
    with TestClient(app) as client:
        assert get_event_bus().subscriber_count() == 0
        response = client.post("/proactive/run")
        assert response.status_code == 200
        body = response.json()
        assert body["fired"] is False
        assert body["reply"] == ""


def test_proactive_reset_clears_throttle_state() -> None:
    """重置接口要真的清掉节流——用户改完免打扰想立刻验证时靠它。"""
    settings = get_settings()
    store = get_state_store(settings.proactive_state_path)
    store.note_proactive(fired_key="x")
    assert store.snapshot().sent_today == 1

    with TestClient(app) as client:
        assert client.post("/proactive/reset").status_code == 200

    assert store.snapshot().sent_today == 0
    assert store.snapshot().fired == {}


def test_runner_status_reports_all_triggers() -> None:
    """触发器清单要能被界面读到（它是「它会怎么主动找我」的唯一说明）。"""
    ids = {item["id"] for item in ProactiveRunner().status()["triggers"]}
    assert ids == {
        "daily-greeting",
        "idle-checkin",
        "memory-followup",
        "late-night",
        # 行踪驱动的两个（见 docs/proactive-multimodal.md §5.2）
        "back-from-away",
        "activity-shift",
        "now-playing",
        "low-battery",
    }


# ---------- 流式接口（ASGI 层驱动）----------


def test_stream_headers_and_ready_frame() -> None:
    async def scenario() -> None:
        session = _SseSession()
        try:
            await session.open(PERSONA)
            assert session.status == 200
            assert session.headers["content-type"].startswith("text/event-stream")
            # 关掉代理缓冲：否则事件会被攒在 nginx 里，主动消息「迟到」到失去意义
            assert session.headers.get("x-accel-buffering") == "no"
            assert "no-cache" in session.headers.get("cache-control", "")

            frame = session.frames[0]
            assert frame["type"] == EVENT_PERCEPTION_HINT
            assert frame["kind"] == "ready"
            assert frame["scope"] == event_scope(PERSONA)
        finally:
            await session.aclose()

    asyncio.run(scenario())


def test_scope_follows_persona() -> None:
    """作用域必须跟着角色走——切到 B 角色还收到 A 的话，界面上无法解释。"""

    async def scenario() -> None:
        session = _SseSession()
        try:
            await session.open("another-persona")
            assert session.frames[0]["scope"] == event_scope("another-persona")
        finally:
            await session.aclose()

    asyncio.run(scenario())


def test_published_event_reaches_subscriber() -> None:
    """端到端：总线收到事件 → 那条连接上真的读得到。

    刻意**不**通过 `/proactive/run` 触发（那要调模型、还要过闸门），
    而是直接往总线里发一条——本用例要验的是**传输层**，
    生成逻辑由 `test_proactive/test_runner.py` 覆盖。
    """

    async def scenario() -> None:
        session = _SseSession()
        try:
            await session.open(PERSONA)
            get_event_bus().publish(
                event_scope(PERSONA),
                Event(type=EVENT_PROACTIVE_MESSAGE, payload={"reply": "还没睡呀？"}),
            )
            await session.wait_frames(2)
            assert session.frames[1]["type"] == EVENT_PROACTIVE_MESSAGE
            assert session.frames[1]["reply"] == "还没睡呀？"
        finally:
            await session.aclose()

    asyncio.run(scenario())


def test_scope_isolation_between_personas() -> None:
    """不同角色的事件房间互不串门。"""

    async def scenario() -> None:
        session = _SseSession()
        try:
            await session.open("a")
            bus = get_event_bus()
            # 发到**另一个**角色的房间
            bus.publish(event_scope("b"), Event(type="leaked", payload={"x": 1}))
            # 再发一条自己的，作为「流仍然活着」的同步点：
            # 若隔离失效，读到的第二条会是那条泄漏的事件
            bus.publish(event_scope("a"), Event(type="own", payload={"x": 1}))
            await session.wait_frames(2)
            assert [frame["type"] for frame in session.frames[1:]] == ["own"]
        finally:
            await session.aclose()

    asyncio.run(scenario())


def test_subscription_released_after_disconnect() -> None:
    """流断开后必须退订：否则会留下永远没人消费的队列，随后每次发布都往里塞数据。"""

    async def scenario() -> None:
        scope = event_scope(PERSONA)
        session = _SseSession()
        await session.open(PERSONA)
        assert get_event_bus().subscriber_count(scope) == 1

        await session.aclose()
        # 取消是异步的，让出一拍等生成器的 finally 跑完
        await asyncio.sleep(0.05)
        assert get_event_bus().subscriber_count(scope) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("persona_id", ["", "  "])
def test_blank_persona_id_still_scopes_safely(persona_id: str) -> None:
    """空角色 id 也走正常路径（后端不强校验）：作用域会退化成 `companion:`，
    但**不会**因此把所有角色的事件混进同一个房间——那是更糟的失败模式。"""

    async def scenario() -> None:
        session = _SseSession()
        try:
            await session.open(persona_id)
            assert session.frames[0]["scope"] == event_scope(persona_id)
        finally:
            await session.aclose()

    asyncio.run(scenario())
