"""感知渲染单测（`app/perception/context.py` 是**唯一渲染处**）。

两条渲染纪律都在这里钉住：
1. **不写行动建议**——「你应该关心用户熬夜」等于把人设写死在感知层；
2. **第二人称事实陈述**，不是机器口吻（「用户正在听《X》」而不是
   「检测到媒体播放事件」）。
"""

from datetime import timedelta, timezone

from app.perception.context import render_perception, summarize
from app.perception.models import DesktopContext, PerceptionEvent

TZ = timezone(timedelta(hours=8))


def _desktop_event(context: DesktopContext) -> PerceptionEvent:
    return PerceptionEvent(
        source="desktop", kind="desktop_context", payload=context.model_dump()
    )


def _song(title: str, artist: str = "") -> PerceptionEvent:
    return PerceptionEvent(
        source="desktop", kind="now_playing", payload={"title": title, "artist": artist}
    )


def _clock(
    *, date: str = "2026-09-29", hour: int = 22, minute: int = 13, weekday: int = 1
) -> PerceptionEvent:
    """时钟事实（source=clock，由 `app/perception/clock.py` 产出）。"""
    return PerceptionEvent(
        source="clock",
        kind="local_time",
        payload={
            "date": date,
            "hour": hour,
            "minute": minute,
            "weekday": weekday,
            "origin": "server",
        },
    )


def test_empty_renders_empty_string() -> None:
    """空串是「零开销」的前提：该层不产生任何字符，组装结果逐字一致。"""
    assert render_perception([]) == ""


def test_song_with_artist() -> None:
    rendered = render_perception([_song("起风了", "买辣椒也用券")])
    assert rendered == "- 用户正在听《起风了》—— 买辣椒也用券"


def test_song_without_artist() -> None:
    assert render_perception([_song("起风了")]) == "- 用户正在听《起风了》"


def test_blank_song_is_skipped() -> None:
    assert render_perception([_song("   ")]) == ""


class TestClock:
    """时间事实（§4.8）：日期 + 星期 + 时段三样都给。"""

    def test_full_rendering(self) -> None:
        assert render_perception([_clock()]) == "- 现在是 9 月 29 日 星期二 22:13，晚上"

    def test_periods_cover_the_day(self) -> None:
        cases = {
            6: "清晨",
            9: "上午",
            13: "中午",
            15: "下午",
            19: "傍晚",
            22: "晚上",
            2: "深夜",
            23: "深夜",
        }
        for hour, label in cases.items():
            rendered = render_perception([_clock(hour=hour)])
            assert rendered.endswith(f"，{label}"), (hour, rendered)

    def test_weekday_name(self) -> None:
        # 0 = 周一，6 = 周日
        assert "星期一" in render_perception([_clock(weekday=0)])
        assert "星期日" in render_perception([_clock(weekday=6)])

    def test_broken_payload_is_skipped(self) -> None:
        """坏数据只跳过这一条，不阻断整轮对话。"""
        broken = PerceptionEvent(source="clock", kind="local_time", payload={"date": "x"})
        assert render_perception([broken]) == ""
        assert render_perception([broken, _song("晴天")]) == "- 用户正在听《晴天》"

    def test_local_time_is_not_duplicated(self) -> None:
        """桌面情景里的 `local_time` 不再单独渲染——时间只有一个来源（clock）。"""
        rendered = render_perception([_desktop_event(DesktopContext(local_time="02:14"))])
        assert rendered == ""


def test_low_battery_unplugged() -> None:
    rendered = render_perception(
        [_desktop_event(DesktopContext(battery_percent=15.0, battery_charging=False))]
    )
    assert "15%" in rendered
    assert "未充电" in rendered


def test_battery_omitted_when_healthy_or_charging() -> None:
    """满电、充电中、或读不到电池（台式机）都不提——说了也只是噪音。"""
    healthy = render_perception(
        [_desktop_event(DesktopContext(battery_percent=80.0, battery_charging=False))]
    )
    charging = render_perception(
        [_desktop_event(DesktopContext(battery_percent=15.0, battery_charging=True))]
    )
    unknown = render_perception(
        [_desktop_event(DesktopContext(battery_percent=15.0, battery_charging=None))]
    )
    assert healthy == ""
    assert charging == ""
    assert unknown == ""


def test_idle_only_mentioned_when_long_enough() -> None:
    short = render_perception([_desktop_event(DesktopContext(idle_seconds=60))])
    long = render_perception([_desktop_event(DesktopContext(idle_seconds=900))])
    assert short == ""
    assert "15 分钟没有操作" in long


def test_idle_over_an_hour_reads_naturally() -> None:
    rendered = render_perception([_desktop_event(DesktopContext(idle_seconds=7800))])
    assert "2 小时 10 分钟" in rendered


class TestWindowTitlePrivacy:
    """窗口标题默认**不注入**：歌名是用户主动播放的，窗口标题可能是病历页面。"""

    def test_hidden_by_default(self) -> None:
        context = DesktopContext(foreground_title="关于体检报告的几点说明")
        assert render_perception([_desktop_event(context)]) == ""

    def test_shown_when_explicitly_enabled(self) -> None:
        context = DesktopContext(foreground_title="项目周报 - 文档")
        rendered = render_perception([_desktop_event(context)], include_window_title=True)
        assert "项目周报" in rendered

    def test_falls_back_to_process_name(self) -> None:
        context = DesktopContext(foreground_process="Code.exe")
        rendered = render_perception([_desktop_event(context)], include_window_title=True)
        assert "Code.exe" in rendered


def test_user_speech_is_not_duplicated() -> None:
    """语音转写已经作为 user_input 进了对话，不再重复注入。"""
    event = PerceptionEvent(source="asr", kind="user_speech", payload={"text": "你好"})
    assert render_perception([event]) == ""


def test_multiple_facts_are_line_prefixed() -> None:
    rendered = render_perception(
        [
            _clock(),
            _song("起风了", "买辣椒也用券"),
            _desktop_event(
                DesktopContext(
                    local_time="02:14", battery_percent=10.0, battery_charging=False
                )
            ),
        ]
    )
    lines = rendered.splitlines()
    assert len(lines) == 3
    assert all(line.startswith("- ") for line in lines)


def test_broken_desktop_payload_is_skipped() -> None:
    """坏数据只跳过这一条，不阻断整轮对话。"""
    broken = PerceptionEvent(source="desktop", kind="desktop_context", payload={"x": []})
    rendered = render_perception([broken, _song("晴天")])
    assert rendered == "- 用户正在听《晴天》"


def test_render_has_no_action_advice() -> None:
    """渲染只陈述事实——怎么用它们是人设的事（它才是「是谁」的那一层）。"""
    rendered = render_perception([_clock(hour=2), _desktop_event(DesktopContext(idle_seconds=5))])
    for advice in ("建议", "应该", "可以问问", "关心"):
        assert advice not in rendered


def _activity(segments: list[tuple[str, int]], *, hours: float = 3.0) -> PerceptionEvent:
    return PerceptionEvent(
        source="activity",
        kind="recent_activity",
        payload={
            "hours": hours,
            "segments": [
                {"process": name, "category": "dev", "title": "", "seconds": seconds,
                 "ongoing": index == len(segments) - 1}
                for index, (name, seconds) in enumerate(segments)
            ],
        },
    )


def _profile(
    tops: list[tuple[str, int]],
    *,
    total: int = 36000,
    peak: tuple[int, int] | None = None,
    night_owl: bool = False,
    days: int = 7,
) -> PerceptionEvent:
    return PerceptionEvent(
        source="profile",
        kind="activity_profile",
        payload={
            "days": days,
            "total_seconds": total,
            "top_processes": [[name, seconds] for name, seconds in tops],
            "peak_window": list(peak) if peak else None,
            "night_owl": night_owl,
        },
    )


class TestActivity:
    """行踪（§4.9）：只报「用得最多的那一个」，一屏能读完。"""

    def test_reports_main_process_with_duration(self) -> None:
        rendered = render_perception([_activity([("Code.exe", 4800)])])
        assert rendered == "- 最近 3 小时，他用得最多的是 Code.exe（约 1 小时 20 分钟）"

    def test_minor_processes_listed_only_when_meaningful(self) -> None:
        """一闪而过的窗口（不足 5 分钟）不进提示词。"""
        brief = render_perception([_activity([("Code.exe", 4800), ("cmd.exe", 60)])])
        assert "cmd.exe" not in brief

        real = render_perception([_activity([("Code.exe", 4800), ("chrome.exe", 1200)])])
        assert "也用过 chrome.exe" in real

    def test_tiny_duration_is_omitted(self) -> None:
        """段刚开出来时是「0 秒」——写进提示词只会让模型盯着那个数字看。"""
        rendered = render_perception([_activity([("Code.exe", 0)])])
        assert rendered == "- 最近 3 小时，他用得最多的是 Code.exe"

    def test_empty_payload_is_skipped(self) -> None:
        empty = PerceptionEvent(source="activity", kind="recent_activity", payload={})
        assert render_perception([empty]) == ""


class TestProfile:
    """偏好画像（§4.9）：每一句都是有数据支撑的计数，没有一句是推断。"""

    def test_top_process_and_secondary(self) -> None:
        rendered = render_perception([_profile([("Code.exe", 43200), ("chrome.exe", 10800)])])
        assert rendered.startswith("- 最近 7 天，他最常用 Code.exe（约 12 小时）")
        assert "其次是 chrome.exe（约 3 小时）" in rendered

    def test_noise_is_not_a_preference(self) -> None:
        """第二名占比过低时不说「其次是」——否则会把噪音当偏好。"""
        rendered = render_perception([_profile([("Code.exe", 43200), ("calc.exe", 60)])])
        assert "其次是" not in rendered

    def test_peak_window_and_night_owl(self) -> None:
        rendered = render_perception([_profile([("Code.exe", 43200)], peak=(20, 23), night_owl=True)])
        assert "他通常在 20:00–23:00 这段时间用电脑" in rendered
        assert "深夜（0–6 点）他也常在电脑前" in rendered

    def test_empty_is_skipped(self) -> None:
        empty = PerceptionEvent(source="profile", kind="activity_profile", payload={})
        assert render_perception([empty]) == ""


class TestSummarize:
    def test_prefers_song(self) -> None:
        assert summarize([_song("起风了", "买辣椒也用券")]).startswith("用户正在听")

    def test_falls_back_to_window_then_clock(self) -> None:
        """徽标优先级：正在听什么 > 当前窗口 > 现在几点（与事件顺序无关）。

        窗口标题在**徽标里**照常显示——徽标是给用户自己看的；
        `include_window_title` 管的是「要不要让**模型**看见」。
        """
        window = _desktop_event(DesktopContext(foreground_title="项目周报"))
        assert summarize([_clock(), window]) == "用户当前打开着「项目周报」"
        assert summarize([window, _clock()]) == "用户当前打开着「项目周报」"
        assert summarize([_clock()]) == "现在是 9 月 29 日 星期二 22:13，晚上"
        assert summarize([_song("起风了"), window, _clock()]) == "用户正在听《起风了》"

    def test_empty(self) -> None:
        assert summarize([]) == ""
