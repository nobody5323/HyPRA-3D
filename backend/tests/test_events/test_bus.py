"""事件总线单测：投递、退订、队列满时的丢弃策略、SSE 帧格式。

异步用例统一用 `asyncio.run` 包在同步函数里：项目现有测试全是同步的，
不为几个用例引入 `pytest-asyncio` 的配置面。
"""

import asyncio

from app.events.bus import Event, EventBus
from app.events.types import EVENT_PROACTIVE_MESSAGE, event_scope


def test_event_sse_frame_format() -> None:
    """SSE 帧必须同时带 `event:`（前端 addEventListener 用）与 `data:`（JSON）。"""
    frame = Event(type="proactive_message", payload={"reply": "还没睡呀？"}).to_sse()
    assert frame.startswith("event: proactive_message\n")
    assert "data: " in frame
    assert frame.endswith("\n\n")
    assert "还没睡呀？" in frame  # 中文不转义（ensure_ascii=False）


def test_publish_without_subscriber_is_not_an_error() -> None:
    """桌宠窗没开时没人订阅——这是正常状态，不是错误。"""
    bus = EventBus()
    assert bus.publish("companion:x", Event(type="t")) == 0
    assert bus.subscriber_count() == 0


def test_publish_delivers_to_scope_only() -> None:
    """作用域隔离：切换角色后不该收到另一个角色的消息。"""

    async def scenario() -> None:
        bus = EventBus()
        async with bus.subscribe("companion:a") as queue_a:
            async with bus.subscribe("companion:b") as queue_b:
                delivered = bus.publish("companion:a", Event(type="t", payload={"n": 1}))
                assert delivered == 1
                event = queue_a.get_nowait()
                assert event.payload["n"] == 1
                assert queue_b.empty()

    asyncio.run(scenario())


def test_multiple_subscribers_all_receive() -> None:
    async def scenario() -> None:
        bus = EventBus()
        async with bus.subscribe("s") as first:
            async with bus.subscribe("s") as second:
                assert bus.subscriber_count("s") == 2
                bus.publish("s", Event(type="t"))
                assert first.qsize() == 1
                assert second.qsize() == 1

    asyncio.run(scenario())


def test_unsubscribe_on_context_exit() -> None:
    """漏退订会留下永远没人消费的队列，随后每次发布都往它里面塞数据。"""

    async def scenario() -> None:
        bus = EventBus()
        async with bus.subscribe("s"):
            assert bus.subscriber_count("s") == 1
        assert bus.subscriber_count("s") == 0
        assert bus.scopes() == []

    asyncio.run(scenario())


def test_full_queue_drops_oldest_not_newest() -> None:
    """队列满时丢最旧的一条：实时推送里「最新的」才有价值。

    更重要的推论：**绝不阻塞发布方**——发布方可能是正等着回复的对话路由，
    一个卡住的浏览器标签页不该把整条链路拖死。
    """

    async def scenario() -> None:
        bus = EventBus(queue_size=2)
        async with bus.subscribe("s") as queue:
            for index in range(4):
                bus.publish("s", Event(type="t", payload={"n": index}))
            remaining = [queue.get_nowait().payload["n"] for _ in range(2)]
            assert remaining == [2, 3]

    asyncio.run(scenario())


def test_publish_from_other_thread() -> None:
    """跨线程发布：记忆写入跑在 `to_thread` 里，它可能需要在完成后推一条事件。"""

    async def scenario() -> None:
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        async with bus.subscribe("s") as queue:
            import threading

            thread = threading.Thread(
                target=lambda: bus.publish("s", Event(type="t", payload={"from": "thread"}))
            )
            thread.start()
            thread.join()
            # call_soon_threadsafe 排入循环，让出一拍才可见
            await asyncio.sleep(0)
            assert queue.get_nowait().payload["from"] == "thread"

    asyncio.run(scenario())


def test_publish_from_thread_without_loop_is_dropped() -> None:
    """没绑定循环时跨线程发布会丢弃并告警——「事件丢了」远好过猜测一个循环。"""

    async def scenario() -> None:
        bus = EventBus()
        import threading

        results: list[int] = []
        thread = threading.Thread(
            target=lambda: results.append(bus.publish("s", Event(type="t")))
        )
        thread.start()
        thread.join()
        assert results == [0]

    asyncio.run(scenario())


def test_unbind_loop_stops_thread_publishing() -> None:
    async def scenario() -> None:
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        bus.unbind_loop()
        assert bus._publish_from_thread("s", Event(type="t")) == 0

    asyncio.run(scenario())


def test_event_scope_keeps_prefix() -> None:
    """前缀不能省：将来会有 `session:{id}` 这类作用域，去掉就分不出归属。"""
    assert event_scope("therapist-elder-sister") == "companion:therapist-elder-sister"
    assert event_scope("  x  ") == "companion:x"
    assert event_scope("") == "companion:"


def test_event_type_constants() -> None:
    """前后端契约字符串（改这里必须同步前端 lib/api/events.ts）。"""
    assert EVENT_PROACTIVE_MESSAGE == "proactive_message"
