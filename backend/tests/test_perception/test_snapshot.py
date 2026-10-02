"""感知快照单测：TTL 过期、同源覆盖、开关关掉即清空。

这一层是「感知是易失的」这条口径的落地点——
5 分钟前听过的歌不该继续出现在上下文里（记忆要留，感知要忘）。
"""

from datetime import datetime, timedelta, timezone

from app.perception.models import DesktopContext, PerceptionEvent
from app.perception.snapshot import PerceptionSnapshot

TZ = timezone(timedelta(hours=8))


def _at(minute: int = 0) -> datetime:
    return datetime(2026, 9, 29, 14, minute, tzinfo=TZ)


def test_empty_snapshot() -> None:
    snapshot = PerceptionSnapshot()
    assert snapshot.current() == []
    assert snapshot.desktop() is None
    assert len(snapshot) == 0


def test_record_and_read_back() -> None:
    snapshot = PerceptionSnapshot()
    event = PerceptionEvent(source="asr", kind="user_speech", payload={"text": "你好"})
    snapshot.record(event)
    assert len(snapshot.current()) == 1


def test_same_source_kind_overwrites() -> None:
    """快照回答的是「**现在**是什么情况」，不是「刚才发生过什么」。"""
    snapshot = PerceptionSnapshot()
    for index in range(3):
        snapshot.record(
            PerceptionEvent(source="desktop", kind="now_playing", payload={"n": index})
        )
    assert len(snapshot) == 1
    assert snapshot.current()[0].payload["n"] == 2


def test_expired_events_are_invisible() -> None:
    snapshot = PerceptionSnapshot()
    snapshot.record(
        PerceptionEvent(
            source="desktop",
            kind="now_playing",
            payload={"title": "起风了"},
            observed_at=_at(0),
            ttl_seconds=60,
        )
    )
    assert len(snapshot.current(now=_at(0))) == 1
    assert snapshot.current(now=_at(5)) == []
    # 过期后 get 也拿不到
    assert snapshot.get("desktop", "now_playing", now=_at(5)) is None


def test_desktop_context_round_trip() -> None:
    snapshot = PerceptionSnapshot()
    context = DesktopContext(
        now_playing_title="起风了",
        now_playing_artist="买辣椒也用券",
        local_time="14:00",
        battery_percent=15.0,
        battery_charging=False,
    )
    snapshot.record_desktop(context)

    restored = snapshot.desktop()
    assert restored is not None
    assert restored.now_playing_title == "起风了"
    assert restored.battery_percent == 15.0


def test_desktop_report_also_records_now_playing() -> None:
    """歌曲被单独记成一条事实：它是「可聊的由头」，与电量/时段消费方式不同。"""
    snapshot = PerceptionSnapshot()
    snapshot.record_desktop(DesktopContext(now_playing_title="起风了", local_time="14:00"))

    kinds = {event.kind for event in snapshot.current()}
    assert "now_playing" in kinds
    assert "desktop_context" in kinds
    song = snapshot.get("desktop", "now_playing")
    assert song is not None and song.payload["title"] == "起风了"


def test_stopping_playback_clears_song() -> None:
    """歌停了要一并清掉——继续留着会让模型以为「他还在听那首歌」。"""
    snapshot = PerceptionSnapshot()
    snapshot.record_desktop(DesktopContext(now_playing_title="起风了"))
    snapshot.record_desktop(DesktopContext(now_playing_title=""))

    assert snapshot.get("desktop", "now_playing") is None
    assert snapshot.get("desktop", "desktop_context") is not None


def test_clear_by_source() -> None:
    """§4.6 的红线：关掉开关后采集链路立即停止，**已有数据也要清掉**。"""
    snapshot = PerceptionSnapshot()
    snapshot.record_desktop(DesktopContext(now_playing_title="起风了", local_time="14:00"))
    snapshot.record(PerceptionEvent(source="asr", kind="user_speech"))

    # 桌面源有两条（desktop_context + now_playing），asr 一条
    assert snapshot.clear(source="desktop") == 2
    remaining = snapshot.current()
    assert len(remaining) == 1
    assert remaining[0].source == "asr"


def test_clear_all() -> None:
    snapshot = PerceptionSnapshot()
    snapshot.record_desktop(DesktopContext(local_time="14:00"))
    assert snapshot.clear() >= 1
    assert len(snapshot) == 0


def test_max_events_is_bounded() -> None:
    """防某个源不断造出新 kind 把内存撑大。"""
    snapshot = PerceptionSnapshot(max_events=3)
    for index in range(10):
        snapshot.record(
            PerceptionEvent(
                source="desktop",
                kind=f"kind-{index}",
                observed_at=_at(min(index, 59)),
            )
        )
    assert len(snapshot) == 3


def test_current_is_sorted_by_observation() -> None:
    snapshot = PerceptionSnapshot()
    snapshot.record(PerceptionEvent(source="a", kind="k", observed_at=_at(12)))
    snapshot.record(PerceptionEvent(source="b", kind="k", observed_at=_at(10)))
    # 显式给 now：默认取真实时钟，而夹具用的是固定时刻。
    # 取 14:13（两条都在默认 300s TTL 内），否则会被判过期而读不到。
    events = snapshot.current(now=_at(13))
    assert [event.source for event in events] == ["b", "a"]
