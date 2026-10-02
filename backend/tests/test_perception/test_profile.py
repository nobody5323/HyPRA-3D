"""偏好画像单测（`app/perception/profile.py`）。

要钉住的四条：
1. **是计数不是推断**——每一句都能追到具体记录（因此聚合必须是纯函数、可复现）；
2. **样本不足就不出画像**（用两分钟的行踪说「他最常用 Code.exe」是不诚实的）；
3. **跨零点的段按小时摊分**（「夜猫子」这个判据全靠它）；
4. **峰值时段允许跨零点**（22:00–01:00 才是夜猫子的峰值，不能写成 22:00–22:00）。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.perception.profile import (
    ActivityProfile,
    build_profile,
)
from app.perception.timeline import (
    CATEGORY_DEV,
    CATEGORY_GAME,
    CATEGORY_MEDIA,
    ActivitySegment,
)

TZ = timezone(timedelta(hours=8))


def _at(hour: int, minute: int = 0, *, day: int = 29) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def _seg(
    process: str,
    category: str,
    *,
    start: datetime,
    end: datetime | None = None,
) -> ActivitySegment:
    return ActivitySegment(
        process=process,
        category=category,
        title="",
        started_at=start.isoformat(),
        ended_at=(end or start).isoformat(),
        ongoing=end is None,
    )


NOW = _at(23, 0)


class TestBuildProfile:
    def test_ranks_processes_by_time(self) -> None:
        profile = build_profile(
            [
                _seg("Code.exe", CATEGORY_DEV, start=NOW - timedelta(hours=5), end=NOW - timedelta(hours=3)),
                _seg("chrome.exe", CATEGORY_DEV, start=NOW - timedelta(hours=3), end=NOW - timedelta(hours=2)),
            ],
            now=NOW,
        )
        assert [name for name, _ in profile.top_processes] == ["Code.exe", "chrome.exe"]
        assert profile.top_processes[0][1] == pytest.approx(2 * 3600, abs=2)

    def test_below_minimum_yields_no_conclusions(self) -> None:
        """样本不足：只回总量，**不出任何结论**（宁可不说，也不要说错）。"""
        profile = build_profile(
            [_seg("Code.exe", CATEGORY_DEV, start=NOW - timedelta(minutes=2), end=NOW)],
            now=NOW,
            min_seconds=1800.0,
        )
        assert profile.total_seconds == pytest.approx(120, abs=2)
        assert profile.top_processes == ()
        assert not profile.usable

    def test_ongoing_segment_counts_up_to_now(self) -> None:
        profile = build_profile(
            [_seg("Code.exe", CATEGORY_DEV, start=NOW - timedelta(hours=2))],
            now=NOW,
        )
        assert profile.total_seconds == pytest.approx(2 * 3600, abs=2)

    def test_clips_to_window(self) -> None:
        """只统计落在回看窗口内的部分——不然「最近 7 天」会把更早的也算进来。"""
        profile = build_profile(
            [_seg("Code.exe", CATEGORY_DEV, start=NOW - timedelta(days=10), end=NOW)],
            now=NOW,
            days=7,
        )
        assert profile.total_seconds == pytest.approx(7 * 86400, abs=5)

    def test_ignores_segments_without_process(self) -> None:
        profile = build_profile(
            [_seg("", CATEGORY_DEV, start=NOW - timedelta(hours=2), end=NOW)],
            now=NOW,
        )
        assert profile.total_seconds == 0


class TestHourBuckets:
    def test_segment_spanning_midnight_is_split_by_hour(self) -> None:
        """22:50 → 00:20 的段要摊到 22 / 23 / 0 三个整点上。"""
        profile = build_profile(
            [
                _seg(
                    "Code.exe",
                    CATEGORY_DEV,
                    start=_at(22, 50, day=28),
                    end=_at(0, 20, day=29),
                )
            ],
            now=NOW,
            days=7,
        )
        # 22 点 10 分钟、23 点 60 分钟、0 点 20 分钟 → 深夜占比 20/90
        assert profile.night_ratio == pytest.approx(20 / 90, abs=0.01)

    def test_night_owl_flag(self) -> None:
        night = build_profile(
            [
                _seg("Code.exe", CATEGORY_DEV, start=_at(0, 0, day=28), end=_at(3, 0, day=28)),
                _seg("Code.exe", CATEGORY_DEV, start=_at(10, 0, day=28), end=_at(11, 0, day=28)),
            ],
            now=NOW,
            days=7,
        )
        day = build_profile(
            [_seg("Code.exe", CATEGORY_DEV, start=_at(10, 0, day=28), end=_at(14, 0, day=28))],
            now=NOW,
            days=7,
        )
        assert night.night_owl
        assert not day.night_owl


class TestPeakWindow:
    def test_picks_the_busiest_three_hours(self) -> None:
        profile = build_profile(
            [
                _seg("Code.exe", CATEGORY_DEV, start=_at(20, 0, day=28), end=_at(23, 0, day=28)),
                _seg("Code.exe", CATEGORY_DEV, start=_at(9, 0, day=28), end=_at(10, 0, day=28)),
            ],
            now=NOW,
            days=7,
        )
        assert profile.peak_window == (20, 23)

    def test_window_may_cross_midnight(self) -> None:
        """夜猫子的峰值是 22:00–01:00，写成 22:00–22:00 就没了意义。"""
        profile = build_profile(
            [
                _seg("Code.exe", CATEGORY_DEV, start=_at(22, 0, day=28), end=_at(1, 0, day=29)),
            ],
            now=NOW,
            days=7,
        )
        assert profile.peak_window == (22, 1)

    def test_no_data_means_no_window(self) -> None:
        assert build_profile([], now=NOW).peak_window is None


class TestActiveHours:
    def test_only_hours_over_the_threshold(self) -> None:
        profile = build_profile(
            [
                _seg("Code.exe", CATEGORY_DEV, start=_at(9, 0, day=28), end=_at(12, 0, day=28)),
                _seg("Code.exe", CATEGORY_DEV, start=_at(15, 0, day=28), end=_at(15, 5, day=28)),
            ],
            now=NOW,
            days=7,
            active_hour_seconds=900.0,
        )
        assert profile.active_hours == (9, 10, 11)


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        """纯函数：同样的段聚两次结果必须一致（可解释的前提）。"""
        segments = [
            _seg("Code.exe", CATEGORY_DEV, start=_at(9, 0, day=28), end=_at(12, 0, day=28)),
            _seg("steam.exe", CATEGORY_GAME, start=_at(21, 0, day=28), end=_at(23, 0, day=28)),
        ]
        first = build_profile(segments, now=NOW, days=7)
        second = build_profile(segments, now=NOW, days=7)
        assert first == second

    def test_tie_is_broken_by_name(self) -> None:
        """时长相同按名字排序——否则顺序会随字典插入顺序漂移，结果不可复现。"""
        segments = [
            _seg("b.exe", CATEGORY_DEV, start=_at(9, 0, day=28), end=_at(10, 0, day=28)),
            _seg("a.exe", CATEGORY_DEV, start=_at(10, 0, day=28), end=_at(11, 0, day=28)),
        ]
        profile = build_profile(segments, now=NOW, days=7)
        assert [name for name, _ in profile.top_processes] == ["a.exe", "b.exe"]


def test_empty_profile_is_not_usable() -> None:
    profile = ActivityProfile(days=7, total_seconds=0.0)
    assert not profile.usable
    assert not profile.night_owl


def test_media_and_game_share_the_leisure_bucket() -> None:
    """类别只影响「他常干什么」这一层——`media` 与 `game` 都是放松，但**不合并**。"""
    profile = build_profile(
        [
            _seg("steam.exe", CATEGORY_GAME, start=_at(20, 0, day=28), end=_at(21, 0, day=28)),
            _seg("vlc.exe", CATEGORY_MEDIA, start=_at(21, 0, day=28), end=_at(22, 0, day=28)),
        ],
        now=NOW,
        days=7,
    )
    assert {name for name, _ in profile.top_categories} == {CATEGORY_GAME, CATEGORY_MEDIA}
