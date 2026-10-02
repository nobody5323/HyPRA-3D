"""节制层（Gate）单测。

这是全仓最值得逐条钉住的一个纯函数：主动沟通的体验成败**九成在节制**，
漏一次是打扰、漏十次是骚扰。每一道闸门都有「过」与「不过」两侧用例。
"""

from datetime import datetime, time, timedelta, timezone

import pytest

from app.proactive.gate import (
    GateContext,
    evaluate_gate,
    in_quiet_hours,
    in_time_window,
    parse_hhmm,
    parse_quiet_hours,
)

TZ = timezone(timedelta(hours=8))


def _at(hour: int, minute: int = 0, *, day: int = 29) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


# ---------- 时间解析 ----------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("09:00", time(9, 0)), ("23:30", time(23, 30)), ("00:00", time(0, 0))],
)
def test_parse_hhmm_ok(spec, expected) -> None:
    assert parse_hhmm(spec) == expected


@pytest.mark.parametrize("spec", ["", "9", "24:00", "09:60", "ab:cd", "9:0:0"])
def test_parse_hhmm_rejects_bad(spec) -> None:
    assert parse_hhmm(spec) is None


def test_parse_quiet_hours_crossing_midnight() -> None:
    assert parse_quiet_hours("23:00-08:00") == (time(23, 0), time(8, 0))


@pytest.mark.parametrize("spec", ["", "23:00", "-", "23:00-", "-08:00"])
def test_parse_quiet_hours_rejects_bad(spec) -> None:
    assert parse_quiet_hours(spec) is None


class TestQuietHours:
    """免打扰时段的区间语义（跨零点 + 左闭右开）。"""

    @pytest.mark.parametrize("hour", [23, 0, 2, 7])
    def test_inside_crossing_window(self, hour: int) -> None:
        assert in_quiet_hours(time(hour, 30), "23:00-08:00") is True

    def test_boundary_end_is_not_quiet(self) -> None:
        """08:00 整**已经可以说话**——「到点解除」晚一分钟正是用户刚打开电脑的时候。"""
        assert in_quiet_hours(time(8, 0), "23:00-08:00") is False

    def test_boundary_start_is_quiet(self) -> None:
        assert in_quiet_hours(time(23, 0), "23:00-08:00") is True

    @pytest.mark.parametrize("hour", [8, 12, 22])
    def test_outside_crossing_window(self, hour: int) -> None:
        assert in_quiet_hours(time(hour, 0), "23:00-08:00") is False

    def test_same_day_window(self) -> None:
        assert in_quiet_hours(time(13, 0), "12:00-14:00") is True
        assert in_quiet_hours(time(15, 0), "12:00-14:00") is False

    def test_degenerate_window_means_all_day(self) -> None:
        """起止相同 = 全天命中：写错了宁可少说话。"""
        assert in_quiet_hours(time(12, 0), "09:00-09:00") is True

    def test_bad_spec_falls_back_to_default(self) -> None:
        """解析失败回落默认窗口，而不是「不设防」。"""
        assert in_quiet_hours(time(2, 0), "写坏了") is True
        assert in_quiet_hours(time(12, 0), "写坏了") is False


def test_in_time_window_alias_behaves_same() -> None:
    assert in_time_window(time(2, 0), "23:00-04:00") is True


# ---------- 闸门 ----------


def _ctx(**overrides) -> GateContext:
    base = dict(
        now=_at(14, 0),
        enabled=True,
        quiet_hours="23:00-08:00",
        min_interval_minutes=90,
        daily_quota=6,
        conversation_window_minutes=2,
    )
    base.update(overrides)
    return GateContext(**base)


def test_gate_allows_plain_case() -> None:
    decision = evaluate_gate(_ctx())
    assert decision.allowed is True
    assert decision.reason == ""


def test_gate_blocks_when_disabled() -> None:
    decision = evaluate_gate(_ctx(enabled=False))
    assert decision.allowed is False
    assert "关闭" in decision.reason


class TestQuietHoursGate:
    def test_blocks_at_night(self) -> None:
        decision = evaluate_gate(_ctx(now=_at(2, 0)))
        assert decision.allowed is False
        assert "免打扰" in decision.reason

    def test_care_exempts_when_user_awake(self) -> None:
        """深夜关怀：免打扰的目的是「别吵醒睡着的人」，不是「看着你熬夜也不说话」。"""
        decision = evaluate_gate(
            _ctx(now=_at(2, 0), care=True, quiet_exempt_care=True, user_active=True)
        )
        assert decision.allowed is True

    def test_care_does_not_exempt_when_user_absent(self) -> None:
        """豁免的前提是**用户此刻清醒**——「现在几点」不是清醒的证据。"""
        decision = evaluate_gate(
            _ctx(now=_at(2, 0), care=True, quiet_exempt_care=True, user_active=False)
        )
        assert decision.allowed is False

    def test_care_does_not_exempt_when_switch_off(self) -> None:
        decision = evaluate_gate(
            _ctx(now=_at(2, 0), care=True, quiet_exempt_care=False, user_active=True)
        )
        assert decision.allowed is False

    def test_non_care_never_exempt(self) -> None:
        decision = evaluate_gate(
            _ctx(now=_at(2, 0), care=False, quiet_exempt_care=True, user_active=True)
        )
        assert decision.allowed is False


class TestCooldown:
    def test_blocks_within_cooldown(self) -> None:
        decision = evaluate_gate(
            _ctx(now=_at(14, 0), last_proactive_at=_at(13, 30))
        )
        assert decision.allowed is False
        assert "不足" in decision.reason

    def test_allows_after_cooldown(self) -> None:
        decision = evaluate_gate(
            _ctx(now=_at(14, 0), last_proactive_at=_at(12, 0))
        )
        assert decision.allowed is True

    def test_no_history_allows(self) -> None:
        assert evaluate_gate(_ctx(last_proactive_at=None)).allowed is True


class TestQuota:
    def test_blocks_at_quota(self) -> None:
        decision = evaluate_gate(_ctx(sent_today=6, daily_quota=6))
        assert decision.allowed is False
        assert "上限" in decision.reason

    def test_allows_below_quota(self) -> None:
        assert evaluate_gate(_ctx(sent_today=5, daily_quota=6)).allowed is True

    def test_zero_quota_blocks_always(self) -> None:
        assert evaluate_gate(_ctx(sent_today=0, daily_quota=0)).allowed is False


class TestConversationWindow:
    def test_blocks_right_after_user_spoke(self) -> None:
        """用户刚说完话时该**回应**，不该另起话头（否则体验上是「抢话」）。"""
        decision = evaluate_gate(
            _ctx(now=_at(14, 0), last_user_message_at=_at(13, 59))
        )
        assert decision.allowed is False
        assert "刚刚说过话" in decision.reason

    def test_allows_after_window(self) -> None:
        assert (
            evaluate_gate(_ctx(now=_at(14, 0), last_user_message_at=_at(13, 50))).allowed
            is True
        )


def test_gate_blocks_when_busy() -> None:
    decision = evaluate_gate(_ctx(busy=True))
    assert decision.allowed is False
    assert "正忙" in decision.reason


def test_gate_order_disabled_wins() -> None:
    """总开关优先级最高：其余条件都满足也不该放行。"""
    decision = evaluate_gate(_ctx(enabled=False, now=_at(14, 0)))
    assert decision.allowed is False
    assert "关闭" in decision.reason
