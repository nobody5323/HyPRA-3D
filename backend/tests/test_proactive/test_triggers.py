"""触发器单测：只验「有没有由头」，不验「该不该说」（那是 gate 的事）。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.memory.cold.models import Fact, FactType
from app.perception.models import DesktopContext, PerceptionEvent
from app.proactive.models import TriggerContext
from app.proactive.triggers import (
    DailyTimeTrigger,
    IdleIntervalTrigger,
    LateNightTrigger,
    LowBatteryTrigger,
    MemoryFollowUpTrigger,
    NowPlayingTrigger,
    build_default_triggers,
)

TZ = timezone(timedelta(hours=8))


def _at(hour: int, minute: int = 0, *, day: int = 29) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def _ctx(**overrides) -> TriggerContext:
    base = dict(now=_at(14, 0), persona_id="tester")
    base.update(overrides)
    return TriggerContext(**base)


# ---------- 定时 ----------


class TestDailyTime:
    def test_fires_after_the_hour(self) -> None:
        trigger = DailyTimeTrigger(at="09:00")
        intent = trigger.evaluate(_ctx(now=_at(9, 30)))
        assert intent is not None
        assert intent.trigger_id == "daily-greeting"
        assert intent.care is False

    def test_does_not_fire_before(self) -> None:
        assert DailyTimeTrigger(at="09:00").evaluate(_ctx(now=_at(8, 59))) is None

    def test_fires_once_per_day(self) -> None:
        trigger = DailyTimeTrigger(at="09:00")
        first = trigger.evaluate(_ctx(now=_at(9, 30)))
        assert first is not None
        fired = {first.dedupe_key: _at(9, 30).isoformat()}
        assert trigger.evaluate(_ctx(now=_at(11, 0), fired=fired)) is None

    def test_fires_again_next_day(self) -> None:
        trigger = DailyTimeTrigger(at="09:00")
        fired = {"daily-greeting:2026-09-28": _at(9, 0, day=28).isoformat()}
        assert trigger.evaluate(_ctx(now=_at(9, 30), fired=fired)) is not None

    def test_invalid_config_disables_trigger(self) -> None:
        """配置写错 = 关闭该触发器，而不是猜一个时刻出来。"""
        assert DailyTimeTrigger(at="写坏了").evaluate(_ctx(now=_at(23, 0))) is None


# ---------- 间隔 ----------


class TestIdleInterval:
    def test_fires_after_silence(self) -> None:
        trigger = IdleIntervalTrigger(hours=6)
        intent = trigger.evaluate(
            _ctx(now=_at(20, 0), last_user_message_at=_at(12, 0))
        )
        assert intent is not None
        assert "8 小时" in intent.reason or "7 小时" in intent.reason

    def test_does_not_fire_when_recent(self) -> None:
        trigger = IdleIntervalTrigger(hours=6)
        assert (
            trigger.evaluate(_ctx(now=_at(20, 0), last_user_message_at=_at(18, 0)))
            is None
        )

    def test_never_fires_without_any_history(self) -> None:
        """从未聊过就不主动开口：第一次接触应该由用户发起。"""
        assert IdleIntervalTrigger(hours=6).evaluate(_ctx(last_user_message_at=None)) is None

    def test_disabled_by_non_positive_hours(self) -> None:
        assert (
            IdleIntervalTrigger(hours=0).evaluate(
                _ctx(last_user_message_at=_at(0, 0))
            )
            is None
        )

    def test_once_per_day(self) -> None:
        trigger = IdleIntervalTrigger(hours=6)
        fired = {"idle-checkin:2026-09-29": _at(14, 0).isoformat()}
        assert (
            trigger.evaluate(
                _ctx(now=_at(20, 0), last_user_message_at=_at(12, 0), fired=fired)
            )
            is None
        )


# ---------- 深夜（关怀型 + 可豁免免打扰）----------


class TestLateNight:
    def test_fires_when_user_active_at_night(self) -> None:
        desktop = DesktopContext(local_time="02:14", idle_seconds=5.0)
        intent = LateNightTrigger().evaluate(_ctx(now=_at(2, 14), desktop=desktop))
        assert intent is not None
        assert intent.care is True
        assert intent.facts == ("现在是 02:14",)

    def test_silent_when_user_absent(self) -> None:
        """屏幕亮着但没输入 → 不开口。这条把「深夜关怀」与「半夜吵醒人」分开了。"""
        desktop = DesktopContext(local_time="02:14", idle_seconds=3600.0)
        assert LateNightTrigger().evaluate(_ctx(now=_at(2, 14), desktop=desktop)) is None

    def test_silent_in_daytime(self) -> None:
        desktop = DesktopContext(local_time="14:00", idle_seconds=5.0)
        assert LateNightTrigger().evaluate(_ctx(now=_at(14, 0), desktop=desktop)) is None

    def test_silent_without_desktop_context(self) -> None:
        """Web 端没有桌面情景 → 该触发器静默失效，不是报错。"""
        assert LateNightTrigger().evaluate(_ctx(now=_at(2, 14))) is None

    def test_once_per_day(self) -> None:
        desktop = DesktopContext(local_time="02:14", idle_seconds=5.0)
        fired = {"late-night:2026-09-29": _at(2, 14).isoformat()}
        assert (
            LateNightTrigger().evaluate(
                _ctx(now=_at(3, 0), desktop=desktop, fired=fired)
            )
            is None
        )


# ---------- 正在听歌 ----------


def _now_playing(title: str, artist: str = "") -> PerceptionEvent:
    return PerceptionEvent(
        source="desktop", kind="now_playing", payload={"title": title, "artist": artist}
    )


class TestNowPlaying:
    def test_fires_on_song(self) -> None:
        intent = NowPlayingTrigger().evaluate(
            _ctx(perception=(_now_playing("起风了", "买辣椒也用券"),))
        )
        assert intent is not None
        assert "起风了" in intent.reason
        assert "买辣椒也用券" in intent.reason
        assert intent.care is False

    def test_same_song_not_repeated_same_day(self) -> None:
        trigger = NowPlayingTrigger()
        fired = {"now-playing:2026-09-29:起风了": _at(14, 0).isoformat()}
        assert (
            trigger.evaluate(
                _ctx(perception=(_now_playing("起风了"),), fired=fired)
            )
            is None
        )

    def test_different_song_fires(self) -> None:
        fired = {"now-playing:2026-09-29:起风了": _at(14, 0).isoformat()}
        assert (
            NowPlayingTrigger().evaluate(
                _ctx(perception=(_now_playing("晴天"),), fired=fired)
            )
            is not None
        )

    def test_silent_without_fact(self) -> None:
        assert NowPlayingTrigger().evaluate(_ctx(perception=())) is None

    def test_ignores_blank_title(self) -> None:
        assert NowPlayingTrigger().evaluate(_ctx(perception=(_now_playing(""),))) is None


# ---------- 低电量 ----------


class TestLowBattery:
    def test_fires_when_low_and_unplugged(self) -> None:
        desktop = DesktopContext(battery_percent=15.0, battery_charging=False)
        intent = LowBatteryTrigger().evaluate(_ctx(desktop=desktop))
        assert intent is not None
        assert intent.care is True

    def test_silent_when_charging(self) -> None:
        desktop = DesktopContext(battery_percent=15.0, battery_charging=True)
        assert LowBatteryTrigger().evaluate(_ctx(desktop=desktop)) is None

    def test_silent_when_healthy(self) -> None:
        desktop = DesktopContext(battery_percent=80.0, battery_charging=False)
        assert LowBatteryTrigger().evaluate(_ctx(desktop=desktop)) is None

    def test_silent_without_battery_info(self) -> None:
        assert LowBatteryTrigger().evaluate(_ctx(desktop=DesktopContext())) is None


# ---------- 记忆到期（进行中事项搁置太久）----------


def _fact(
    *,
    fact_id: str = "f1",
    subject: str = "用户",
    predicate: str = "在准备",
    obj: str = "面试",
    created_at: datetime | None = None,
    last_seen_at: datetime | None = None,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        type=FactType.ONGOING,
        subject=subject,
        predicate=predicate,
        object=obj,
        created_at=created_at or _at(0, day=20),
        last_seen_at=last_seen_at,
    )


class TestMemoryFollowUp:
    def test_fires_when_stale(self) -> None:
        trigger = MemoryFollowUpTrigger(days=3)
        intent = trigger.evaluate(_ctx(ongoing_facts=(_fact(),)))
        assert intent is not None
        assert intent.trigger_id == "memory-followup"
        assert "面试" in intent.reason
        assert intent.care is False  # 凌晨两点问一件私事是奇怪的

    def test_silent_when_recent(self) -> None:
        trigger = MemoryFollowUpTrigger(days=3)
        # 两天前刚被印证过 → 还没到该问的时候
        recent = _fact(last_seen_at=_at(12, day=27))
        assert trigger.evaluate(_ctx(now=_at(14, day=29), ongoing_facts=(recent,))) is None

    def test_uses_last_seen_over_created(self) -> None:
        """「最近被提起过」比「什么时候记下的」更能说明这件事还热着。"""
        trigger = MemoryFollowUpTrigger(days=3)
        fact = _fact(created_at=_at(0, day=1), last_seen_at=_at(0, day=28))
        assert trigger.evaluate(_ctx(ongoing_facts=(fact,))) is None

    def test_asks_only_once_per_fact(self) -> None:
        """问两遍是唠叨——而「没问出来结果」不构成再问的理由。"""
        trigger = MemoryFollowUpTrigger(days=3)
        first = trigger.evaluate(_ctx(ongoing_facts=(_fact(),)))
        assert first is not None
        fired = {first.dedupe_key: _at(14).isoformat()}
        assert trigger.evaluate(_ctx(ongoing_facts=(_fact(),), fired=fired)) is None

    def test_picks_the_stalest_one(self) -> None:
        """一次只问一件；`ongoing_facts` 已按「最久没被提起」升序。"""
        trigger = MemoryFollowUpTrigger(days=3)
        stale = _fact(fact_id="old", obj="搬家", created_at=_at(0, day=1))
        fresh = _fact(fact_id="new", obj="面试", created_at=_at(0, day=20))
        intent = trigger.evaluate(_ctx(ongoing_facts=(stale, fresh)))
        assert intent is not None
        assert "搬家" in intent.reason

    def test_skips_already_asked_and_moves_to_next(self) -> None:
        trigger = MemoryFollowUpTrigger(days=3)
        stale = _fact(fact_id="old", obj="搬家", created_at=_at(0, day=1))
        other = _fact(fact_id="mid", obj="面试", created_at=_at(0, day=10))
        fired = {"memory-followup:old": _at(14, day=28).isoformat()}
        intent = trigger.evaluate(_ctx(ongoing_facts=(stale, other), fired=fired))
        assert intent is not None
        assert "面试" in intent.reason

    def test_naive_timestamp_does_not_crash(self) -> None:
        """`Fact.created_at` 是 naive 的，而 ctx.now 是 aware 的——
        直接相减会抛 TypeError，且只在「记忆里有进行中事项」时才炸。"""
        trigger = MemoryFollowUpTrigger(days=3)
        naive = _fact(created_at=datetime(2026, 9, 20, 10, 0))
        assert naive.created_at.tzinfo is None
        assert trigger.evaluate(_ctx(ongoing_facts=(naive,))) is not None

    def test_disabled_by_non_positive_days(self) -> None:
        assert (
            MemoryFollowUpTrigger(days=0).evaluate(_ctx(ongoing_facts=(_fact(),))) is None
        )

    def test_silent_without_facts(self) -> None:
        assert MemoryFollowUpTrigger(days=3).evaluate(_ctx()) is None

    def test_skips_fact_without_readable_text(self) -> None:
        blank = _fact(subject="", predicate="", obj="")
        assert MemoryFollowUpTrigger(days=3).evaluate(_ctx(ongoing_facts=(blank,))) is None


# ---------- 组装 ----------


def test_defaults_are_tuned_for_an_active_companion() -> None:
    """默认频率是「活跃但不烦人」那一档。

    原值（冷却 90 分钟 / 每日 6 条 / 静默 6 小时）按「宁可少说」定的，
    实际用下来一天 6 条、每条间隔一个半小时，陪伴感偏弱。
    这条断言把调整后的档位钉住——改动它应当是有意识的决定，而不是顺手。
    """
    from app.config import Settings

    settings = Settings()
    assert settings.proactive_min_interval_minutes == 30
    assert settings.proactive_daily_quota == 12
    assert settings.proactive_idle_hours == 3.0
    # 免打扰与「对话中不插话」不动：它们是「不烦人」的底线，不是频率旋钮
    assert settings.proactive_quiet_hours == "23:00-08:00"
    assert settings.proactive_conversation_window_minutes == 2


def test_build_default_triggers_reads_settings(monkeypatch) -> None:
    from app.config import Settings

    settings = Settings(
        proactive_daily_time="07:30",
        proactive_idle_hours=3.0,
        proactive_memory_followup_days=7.0,
        proactive_back_from_away_minutes=45.0,
        proactive_activity_shift_minutes=25.0,
    )
    triggers = build_default_triggers(settings)
    ids = [t.id for t in triggers]
    assert ids == [
        "daily-greeting",
        "idle-checkin",
        "memory-followup",
        "late-night",
        "back-from-away",
        "activity-shift",
        "now-playing",
        "low-battery",
    ]
    assert triggers[0].at == "07:30"
    assert triggers[1].hours == 3.0
    assert triggers[2].days == 7.0
    assert triggers[4].minutes == 45.0
    assert triggers[5].minutes == 25.0


# ---------- 行踪驱动（§4.9）----------


def _segment(
    process: str,
    category: str,
    *,
    start: datetime,
    end: datetime | None = None,
):
    """构造一段行踪（`end=None` = 仍在进行）。"""
    from app.perception.timeline import ActivitySegment

    return ActivitySegment(
        process=process,
        category=category,
        title="",
        started_at=start.isoformat(),
        ended_at=(end or start).isoformat(),
        ongoing=end is None,
    )


def _summary(*segments, away_seconds: float = 0.0):
    from app.perception.timeline import ActivitySummary

    return ActivitySummary(segments=tuple(segments), away_seconds=away_seconds)


class TestBackFromAway:
    """「回来啦」：唯一一种「用户什么都没做、但状态变了」的时刻。"""

    def _trigger(self, minutes: float = 30.0):
        from app.proactive.triggers import BackFromAwayTrigger

        return BackFromAwayTrigger(minutes=minutes)

    def test_fires_when_away_long_enough(self) -> None:
        now = _at(14, 0)
        current = _segment("Code.exe", "dev", start=now - timedelta(minutes=2))
        ctx = _ctx(now=now, activity=_summary(current, away_seconds=3600.0))
        intent = self._trigger().evaluate(ctx)
        assert intent is not None
        assert "1 小时 0 分钟" in intent.reason
        assert any("刚回到电脑前" in fact for fact in intent.facts)

    def test_short_break_does_not_fire(self) -> None:
        """去倒杯水不该被当成「久别重逢」。"""
        now = _at(14, 0)
        current = _segment("Code.exe", "dev", start=now - timedelta(minutes=1))
        ctx = _ctx(now=now, activity=_summary(current, away_seconds=120.0))
        assert self._trigger().evaluate(ctx) is None

    def test_no_activity_source_is_silent(self) -> None:
        """Web 端 / 关了行踪时静默失效（不是报错）。"""
        assert self._trigger().evaluate(_ctx()) is None

    def test_disabled_by_zero(self) -> None:
        now = _at(14, 0)
        current = _segment("Code.exe", "dev", start=now - timedelta(minutes=2))
        ctx = _ctx(now=now, activity=_summary(current, away_seconds=7200.0))
        assert self._trigger(minutes=0).evaluate(ctx) is None

    def test_same_return_does_not_repeat(self) -> None:
        """去重键用当前段的开始时刻：同一次回来只说一句。"""
        now = _at(14, 0)
        current = _segment("Code.exe", "dev", start=now - timedelta(minutes=2))
        intent = self._trigger().evaluate(
            _ctx(now=now, activity=_summary(current, away_seconds=3600.0))
        )
        assert intent is not None
        again = self._trigger().evaluate(
            _ctx(
                now=now,
                activity=_summary(current, away_seconds=3600.0),
                fired={intent.dedupe_key: now.isoformat()},
            )
        )
        assert again is None


class TestActivityShift:
    """「忙完了？」：只做「做事 → 放松」一个方向。"""

    def _trigger(self, minutes: float = 20.0):
        from app.proactive.triggers import ActivityShiftTrigger

        return ActivityShiftTrigger(minutes=minutes)

    def test_fires_from_focus_to_leisure(self) -> None:
        now = _at(21, 0)
        work = _segment(
            "Code.exe", "dev", start=now - timedelta(hours=3), end=now - timedelta(minutes=40)
        )
        game = _segment("steam.exe", "game", start=now - timedelta(minutes=40))
        ctx = _ctx(now=now, activity=_summary(work, game))
        intent = self._trigger().evaluate(ctx)
        assert intent is not None
        assert "Code.exe" in intent.reason and "steam.exe" in intent.reason

    def test_reverse_direction_does_not_fire(self) -> None:
        """娱乐切回工作：用户正要进入状态，开口就是打断。"""
        now = _at(21, 0)
        game = _segment(
            "steam.exe", "game", start=now - timedelta(hours=2), end=now - timedelta(minutes=40)
        )
        work = _segment("Code.exe", "dev", start=now - timedelta(minutes=40))
        ctx = _ctx(now=now, activity=_summary(game, work))
        assert self._trigger().evaluate(ctx) is None

    def test_just_switched_is_too_early(self) -> None:
        """刚切过去一会儿可能只是点开看一眼。"""
        now = _at(21, 0)
        work = _segment(
            "Code.exe", "dev", start=now - timedelta(hours=3), end=now - timedelta(minutes=5)
        )
        game = _segment("steam.exe", "game", start=now - timedelta(minutes=5))
        ctx = _ctx(now=now, activity=_summary(work, game))
        assert self._trigger().evaluate(ctx) is None

    def test_disabled_by_zero(self) -> None:
        now = _at(21, 0)
        work = _segment(
            "Code.exe", "dev", start=now - timedelta(hours=3), end=now - timedelta(minutes=40)
        )
        game = _segment("steam.exe", "game", start=now - timedelta(minutes=40))
        ctx = _ctx(now=now, activity=_summary(work, game))
        assert self._trigger(minutes=0).evaluate(ctx) is None

    def test_no_activity_source_is_silent(self) -> None:
        assert self._trigger().evaluate(_ctx()) is None


class TestActivityGivesGreetingsAReason:
    """有由头才开口：问候带上「他最近在忙什么」（§5.4）。"""

    def test_daily_greeting_carries_activity_and_profile(self) -> None:
        ctx = _ctx(
            now=_at(9, 30),
            activity_fact="最近 3 小时，他用得最多的是 Code.exe（约 2 小时）",
            profile_facts=("最近 7 天，他最常用 Code.exe（约 12 小时）",),
        )
        intent = DailyTimeTrigger(at="09:00").evaluate(ctx)
        assert intent is not None
        assert "Code.exe" in intent.reason
        assert len(intent.facts) == 2

    def test_idle_checkin_carries_activity(self) -> None:
        ctx = _ctx(
            now=_at(14, 0),
            last_user_message_at=_at(9, 0),
            activity_fact="最近 3 小时，他用得最多的是 Code.exe（约 2 小时）",
            profile_facts=("最近 7 天，他最常用 Code.exe（约 12 小时）",),
        )
        intent = IdleIntervalTrigger(hours=3.0).evaluate(ctx)
        assert intent is not None
        assert "Code.exe" in intent.reason
        # 画像只由「每日问候」带一次，这里不重复占预算
        assert intent.facts == ("最近 3 小时，他用得最多的是 Code.exe（约 2 小时）",)

    def test_without_activity_it_still_works(self) -> None:
        """没有行踪（Web 端 / 关了开关）时照常问候，只是少了由头。"""
        intent = DailyTimeTrigger(at="09:00").evaluate(_ctx(now=_at(9, 30)))
        assert intent is not None
        assert intent.facts == ()


@pytest.mark.parametrize(
    "trigger",
    [
        DailyTimeTrigger(at="09:00"),
        IdleIntervalTrigger(hours=6),
        MemoryFollowUpTrigger(days=3),
        LateNightTrigger(),
        NowPlayingTrigger(),
        LowBatteryTrigger(),
    ],
)
def test_triggers_are_pure_and_return_none_on_empty_context(trigger) -> None:
    """空上下文（没有任何历史与感知）时，任何触发器都不该凭空开口。

    时刻取 07:00（早于默认的 09:00 问候时刻）：`daily-greeting` 是唯一
    **不需要任何历史**就能触发的触发器，用 14:00 会让它命中，
    那样这条用例就验不到「没有依据就不开口」了。
    """
    assert trigger.evaluate(_ctx(now=_at(7, 0))) is None
