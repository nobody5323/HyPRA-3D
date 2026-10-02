"""行踪单测（`app/perception/timeline.py`）。

要钉住的三条：
1. **按段记，不按次记**（同一进程连续使用只有一段）；
2. **空闲必须闭段**，且结束时刻取「最后一次操作」——
   否则会记出「用户连续 8 小时在用 VS Code」，而那时人根本不在；
3. **默认不记窗口标题**（那是内容，不是类别）。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.perception.models import DesktopContext
from app.perception.timeline import (
    CATEGORY_BROWSER,
    CATEGORY_DEV,
    CATEGORY_GAME,
    CATEGORY_MEETING,
    CATEGORY_MEDIA,
    CATEGORY_OTHER,
    ActivityTimeline,
    categorize,
)

TZ = timezone(timedelta(hours=8))


def _at(hour: int, minute: int = 0, *, day: int = 29) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def _desktop(process: str, *, idle: float = 0.0, title: str = "") -> DesktopContext:
    return DesktopContext(
        foreground_process=process,
        foreground_title=title,
        idle_seconds=idle,
    )


@pytest.fixture
def timeline(tmp_path):
    return ActivityTimeline(root=tmp_path / "perception")


class TestCategorize:
    def test_known_categories(self) -> None:
        assert categorize("Code.exe") == CATEGORY_DEV
        assert categorize("chrome.exe") == CATEGORY_BROWSER
        assert categorize("steam.exe") == CATEGORY_GAME

    def test_case_insensitive(self) -> None:
        assert categorize("CODE.EXE") == CATEGORY_DEV

    def test_reuses_busy_tables(self) -> None:
        """会议 / 游戏 / 视频三类复用 `busy.py` 的表，不另抄一份。"""
        assert categorize("wemeetapp.exe") == CATEGORY_MEETING
        assert categorize("cloudmusic.exe") == CATEGORY_MEDIA

    def test_unknown_is_other(self) -> None:
        assert categorize("some-random-app.exe") == CATEGORY_OTHER
        assert categorize("") == CATEGORY_OTHER


class TestSegmentation:
    def test_same_process_stays_one_segment(self, timeline) -> None:
        for minute in range(0, 10):
            timeline.observe(_desktop("Code.exe"), now=_at(10, minute))
        segments = timeline.segments(hours=24, now=_at(10, 9))
        assert len(segments) == 1
        assert segments[0].process == "Code.exe"
        assert segments[0].ongoing

    def test_switching_process_closes_and_opens(self, timeline) -> None:
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("Code.exe"), now=_at(10, 30))
        timeline.observe(_desktop("chrome.exe"), now=_at(10, 31))
        segments = timeline.segments(hours=24, now=_at(10, 31))
        assert [s.process for s in segments] == ["Code.exe", "chrome.exe"]
        closed = segments[0]
        assert not closed.ongoing
        # 段的结束时刻取**发现切换的那一刻**（10:31）：采样周期内精确到哪一秒切走
        # 是拿不到的，多算一分钟与少算一分钟都在采样误差内
        assert closed.seconds() == pytest.approx(31 * 60, abs=2)
        assert segments[1].ongoing

    def test_current_file_survives_restart(self, tmp_path) -> None:
        """进程重启不丢正在进行的这一段（当前段单独落一个小文件）。"""
        root = tmp_path / "perception"
        first = ActivityTimeline(root=root)
        first.observe(_desktop("Code.exe"), now=_at(10, 0))
        first.observe(_desktop("Code.exe"), now=_at(10, 20))

        second = ActivityTimeline(root=root)
        current = second.current(now=_at(10, 25))
        assert current is not None
        assert current.process == "Code.exe"
        assert current.seconds(now=_at(10, 25)) == pytest.approx(25 * 60, abs=2)

    def test_empty_process_is_not_recorded(self, timeline) -> None:
        """认不出在做什么就不记（前台窗口拿不到时）。"""
        timeline.observe(_desktop(""), now=_at(10, 0))
        assert timeline.segments(hours=24, now=_at(10, 1)) == []

    def test_very_short_segment_is_dropped(self, timeline) -> None:
        """一闪而过的窗口不记——记了只会污染聚合。"""
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("chrome.exe"), now=_at(10, 0) + timedelta(milliseconds=200))
        segments = timeline.segments(hours=24, now=_at(10, 1))
        assert [s.process for s in segments] == ["chrome.exe"]

    def test_long_segment_is_split(self, timeline) -> None:
        """同一进程超过上限也断开，让「上午 / 下午」在行踪里可分辨。"""
        short = ActivityTimeline(root=timeline._root, max_segment_seconds=3600.0)
        short.observe(_desktop("Code.exe"), now=_at(9, 0))
        short.observe(_desktop("Code.exe"), now=_at(9, 30))
        short.observe(_desktop("Code.exe"), now=_at(10, 1))
        segments = short.segments(hours=24, now=_at(10, 1))
        assert len(segments) == 2


class TestIdleHandling:
    """空闲必须闭段——不然「人不在」会被读成「一直在用」。"""

    def test_idle_closes_segment_at_last_input(self, timeline) -> None:
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("Code.exe"), now=_at(10, 30))
        # 12:00 上报，空闲 3600 秒 → 真实最后一次操作是 11:00
        timeline.observe(_desktop("Code.exe", idle=3600.0), now=_at(12, 0))

        assert timeline.current(now=_at(12, 0)) is None
        segments = timeline.segments(hours=24, now=_at(12, 0))
        assert len(segments) == 1
        assert segments[0].end.hour == 11
        assert segments[0].end.minute == 0

    def test_short_break_does_not_close(self, timeline) -> None:
        """接个电话、去倒杯水不该断开。"""
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("Code.exe", idle=120.0), now=_at(10, 5))
        assert timeline.current(now=_at(10, 5)) is not None

    def test_returning_opens_a_new_segment(self, timeline) -> None:
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("Code.exe"), now=_at(10, 20))
        # 10:30 上报时已空闲 600 秒 → 上一段收在 10:20（真正的最后一次操作）
        timeline.observe(_desktop("Code.exe", idle=600.0), now=_at(10, 30))
        assert timeline.current(now=_at(10, 30)) is None

        timeline.observe(_desktop("Code.exe"), now=_at(11, 0))
        segments = timeline.segments(hours=24, now=_at(11, 0))
        assert len(segments) == 2
        assert segments[0].end.strftime("%H:%M") == "10:20"
        assert segments[-1].ongoing


class TestAwayBeforeCurrent:
    """「离开多久」由段间空隙算出来，**不能**用 idle_seconds（它一回来就归零）。"""

    def test_measures_the_gap(self, timeline) -> None:
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("Code.exe"), now=_at(10, 20))
        # 10:30 上报时已空闲 600 秒 → 上一段收在 10:20
        timeline.observe(_desktop("Code.exe", idle=600.0), now=_at(10, 30))
        # 11:00 回来
        timeline.observe(_desktop("Code.exe"), now=_at(11, 0))
        away = timeline.away_before_current(now=_at(11, 0))
        assert away == pytest.approx(40 * 60, abs=2)

    def test_zero_when_segment_is_old(self, timeline) -> None:
        """段已经开了很久 → 不是「刚回来」，返回 0（避免每 30 秒误判一次）。"""
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        assert timeline.away_before_current(now=_at(10, 30)) == 0.0

    def test_zero_when_there_is_no_previous_segment(self, timeline) -> None:
        """第一次观测（刚启动）不算「回来」。"""
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        assert timeline.away_before_current(now=_at(10, 0)) == 0.0


class TestPrivacy:
    """三条隐私约束（§4.6 同级）。"""

    def test_window_title_not_recorded_by_default(self, tmp_path) -> None:
        timeline = ActivityTimeline(root=tmp_path / "p")
        timeline.observe(
            _desktop("Code.exe", title="关于体检报告的几点说明"), now=_at(10, 0)
        )
        assert timeline.current(now=_at(10, 0)).title == ""

    def test_window_title_recorded_when_enabled(self, tmp_path) -> None:
        timeline = ActivityTimeline(root=tmp_path / "p", include_title=True)
        timeline.observe(_desktop("Code.exe", title="项目周报"), now=_at(10, 0))
        assert timeline.current(now=_at(10, 0)).title == "项目周报"

    def test_clear_removes_everything(self, timeline) -> None:
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("chrome.exe"), now=_at(10, 30))
        assert timeline.clear() >= 1
        assert timeline.segments(hours=24, now=_at(10, 31)) == []
        assert timeline.current(now=_at(10, 31)) is None

    def test_retention_prunes_old_files(self, tmp_path) -> None:
        root = tmp_path / "p"
        root.mkdir(parents=True)
        (root / "activity-2020-01-01.jsonl").write_text("{}\n", encoding="utf-8")

        timeline = ActivityTimeline(root=root, retention_days=7)
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("chrome.exe"), now=_at(10, 30))  # 触发一次 prune
        assert not (root / "activity-2020-01-01.jsonl").exists()

    def test_broken_line_is_skipped(self, timeline) -> None:
        """一行脏数据不该丢掉整天的行踪。"""
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("chrome.exe"), now=_at(10, 30))
        path = timeline._segment_file("2026-09-29")
        path.write_text(
            path.read_text(encoding="utf-8") + "这不是 JSON\n", encoding="utf-8"
        )
        segments = timeline.segments(hours=24, now=_at(10, 31))
        assert [s.process for s in segments] == ["Code.exe", "chrome.exe"]


class TestSummary:
    def test_current_and_previous(self, timeline) -> None:
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        timeline.observe(_desktop("steam.exe"), now=_at(11, 0))
        summary = timeline.summary(hours=24, now=_at(11, 10))
        assert summary.current is not None and summary.current.process == "steam.exe"
        assert summary.previous is not None and summary.previous.process == "Code.exe"

    def test_empty_summary(self, timeline) -> None:
        summary = timeline.summary(hours=24, now=_at(10, 0))
        assert summary.current is None
        assert summary.latest is None


class TestSwitchReconcile:
    """开关对账：关掉行踪 = 停止记录 **且** 清掉已记的内容（幂等）。"""

    def test_noop_when_enabled(self, tmp_path, monkeypatch) -> None:
        from app.config import Settings
        from app.perception.timeline import sync_activity_switch

        timeline = ActivityTimeline(root=tmp_path / "p")
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        monkeypatch.setattr(
            "app.perception.timeline.get_timeline", lambda **kwargs: timeline
        )

        assert sync_activity_switch(settings=Settings()) == 0
        assert timeline.current(now=_at(10, 1)) is not None

    def test_wipes_when_disabled(self, tmp_path, monkeypatch) -> None:
        from app.config import Settings
        from app.perception.timeline import sync_activity_switch

        timeline = ActivityTimeline(root=tmp_path / "p")
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        monkeypatch.setattr(
            "app.perception.timeline.get_timeline", lambda **kwargs: timeline
        )

        settings = Settings(perception_activity_enabled=False)
        assert sync_activity_switch(settings=settings) >= 1
        assert timeline.current(now=_at(10, 1)) is None

    def test_is_idempotent(self, tmp_path, monkeypatch) -> None:
        from app.config import Settings
        from app.perception.timeline import sync_activity_switch

        timeline = ActivityTimeline(root=tmp_path / "p")
        timeline.observe(_desktop("Code.exe"), now=_at(10, 0))
        monkeypatch.setattr(
            "app.perception.timeline.get_timeline", lambda **kwargs: timeline
        )

        settings = Settings(perception_activity_enabled=False)
        sync_activity_switch(settings=settings)
        # 再调一次不该报错、也不该有副作用（可以放心地在多处都调它）
        assert sync_activity_switch(settings=settings) == 0
