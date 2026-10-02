"""感知事实 → 提示词文本（**唯一渲染处**）。

## 为什么必须集中在一处

同一条事实（「正在听歌」）会出现在三个地方：注入提示词、推给界面的徽标、
被触发器读来判断。如果渲染散在各处，就会变成「提示词里写的是 A、
徽标上写的是 B」——而且加一种事实要改三个地方，迟早漏掉一个。

## 三条渲染纪律

1. **不写行动建议**。「你应该关心用户熬夜」这种句子等于把人设写死在感知层——
   怎么用这些事实是 persona 的事（它才是「是谁」的那一层）。
2. **第二人称事实陈述**，不是机器口吻。写「用户正在听《X》」而不是
   「检测到媒体播放事件」——后者会让模型把注意力放在「系统能力」上，
   而不是「这个人此刻在做什么」。
3. **进程名可以进提示词，窗口标题默认不进**。进程名是**类别**
   （`Code.exe` / `chrome.exe`），窗口标题是**内容**（可能是一份体检报告）——
   同 `busy.py` 的判据取舍。所以行踪与画像渲染的是进程名，
   而窗口标题仍然只由 `include_window_title` 单独放行。

输出示例::

    - 现在是 9 月 29 日 星期二 22:13，深夜
    - 用户正在听《起风了》—— 买辣椒也用券
    - 最近 3 小时，他用得最多的是 Code.exe（约 1 小时 20 分钟）
    - 最近 7 天，他最常用 Code.exe（约 12 小时）
    - 设备电量 15%，未充电
"""

from __future__ import annotations

from datetime import datetime

from app.perception.clock import WEEKDAY_NAMES
from app.perception.models import DesktopContext, PerceptionEvent
from app.perception.snapshot import DESKTOP_KIND

#: 「用户已很久没操作」的阈值（秒）：低于这个数不提——刚放下鼠标不算「不在」
IDLE_MENTION_SECONDS = 600.0

#: 行踪里「也用过」的次要应用门槛（秒）：低于它就不列。
#: 5 分钟：一闪而过的窗口（切过去看了一眼）不值得占用提示词。
ACTIVITY_MINOR_SECONDS = 300.0

#: 画像里「其次是」的占比门槛：低于总量这个比例的第二名不列——
#: 否则会写出「他最常用 Code.exe，其次是计算器」这种把噪音当偏好的句子。
PROFILE_SECONDARY_RATIO = 0.15

#: 时段划分（`hour` → 中文）。左闭右开，未命中即为「深夜」。
_PERIOD_RANGES: tuple[tuple[int, int, str], ...] = (
    (5, 8, "清晨"),
    (8, 12, "上午"),
    (12, 14, "中午"),
    (14, 18, "下午"),
    (18, 20, "傍晚"),
    (20, 23, "晚上"),
)


# =============================================================
# 单个事实的渲染
# =============================================================


def _period_of(hour: int) -> str:
    for start, end, label in _PERIOD_RANGES:
        if start <= hour < end:
            return label
    return "深夜"


def _render_now_playing(payload: dict) -> str:
    title = str(payload.get("title") or "").strip()
    artist = str(payload.get("artist") or "").strip()
    if not title:
        return ""
    if artist:
        return f"用户正在听《{title}》—— {artist}"
    return f"用户正在听《{title}》"


def _render_clock(payload: dict) -> str:
    """时间事实 → 「现在是 9 月 29 日 星期二 22:13，深夜」。

    日期、星期、时段三样都给：模型据此才能分清「周末早上」与「工作日深夜」，
    而这两个场景下该说的话完全不同。年份刻意不给——陪伴场景里它几乎无用，
    而每轮多几个 token 是实打实的成本。
    """
    try:
        date = str(payload.get("date") or "")
        hour = int(payload.get("hour", -1))
        minute = int(payload.get("minute", -1))
        weekday = int(payload.get("weekday", -1))
    except (TypeError, ValueError):
        return ""
    if not date or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return ""
    if not 0 <= weekday <= 6:
        return ""

    month = int(date[5:7]) if len(date) >= 10 else 0
    day = int(date[8:10]) if len(date) >= 10 else 0
    stamp = f"{hour:02d}:{minute:02d}"
    period = _period_of(hour)
    if month and day:
        return f"现在是 {month} 月 {day} 日 星期{WEEKDAY_NAMES[weekday]} {stamp}，{period}"
    return f"现在是 {stamp}，{period}"


def _format_duration(seconds: float) -> str:
    """秒 → 「1 小时 20 分钟」/「45 分钟」/「30 秒」。"""
    total = int(max(0.0, seconds))
    if total >= 3600:
        hours, remainder = divmod(total, 3600)
        minutes = remainder // 60
        return f"{hours} 小时 {minutes} 分钟" if minutes else f"{hours} 小时"
    if total >= 60:
        return f"{total // 60} 分钟"
    return f"{total} 秒"


def _format_hours(hours: float) -> str:
    """小时数 → 「3 小时」/「1.5 小时」（去尾零）。"""
    value = float(hours)
    if value == int(value):
        return f"{int(value)} 小时"
    return f"{value:g} 小时"


def render_activity_fact(payload: dict) -> str:
    """最近几小时的行踪 → 一句「他用得最多的是什么」。

    刻意**只报最大的那一个**加最多两个次要的：一屏之内能读完，
    而「他刚才在干什么」本来就是一句话的事。逐段罗列会变成流水账，
    既占预算又不会让模型的回答更贴切。
    """
    segments = payload.get("segments") or []
    hours = payload.get("hours")
    if not isinstance(segments, list) or not segments:
        return ""

    totals: dict[str, float] = {}
    for row in segments:
        if not isinstance(row, dict):
            continue
        name = str(row.get("process") or "").strip()
        if not name:
            continue
        try:
            seconds = float(row.get("seconds") or 0.0)
        except (TypeError, ValueError):
            continue
        totals[name] = totals.get(name, 0.0) + seconds
    if not totals:
        return ""

    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    main, main_seconds = ranked[0]
    prefix = f"最近 {_format_hours(float(hours))}" if hours else "这段时间"
    line = f"{prefix}，他用得最多的是 {main}"
    if main_seconds >= 60:
        # 不足一分钟不报时长：段刚开出来时它是「0 秒」，
        # 写进提示词只会让模型盯着那个数字看（「约 0 秒」是什么？）
        line += f"（约 {_format_duration(main_seconds)}）"

    others = [
        name
        for name, seconds in ranked[1:3]
        if seconds >= ACTIVITY_MINOR_SECONDS
    ]
    if others:
        line += f"，也用过 {'、'.join(others)}"
    return line


def render_profile_facts(payload: dict) -> str:
    """偏好画像 → 最多三行事实（常用应用 / 活跃时段 / 夜猫子）。

    三行都是有数据支撑的**计数**，没有一句是推断。
    尤其刻意不写「他工作很忙」这类结论——那是人设该说的话（见模块文档第 1 条纪律）。
    """
    tops = payload.get("top_processes") or []
    days = payload.get("days")
    if not isinstance(tops, list) or not tops:
        return ""

    def _pair(row) -> tuple[str, float] | None:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            return None
        name = str(row[0] or "").strip()
        if not name:
            return None
        try:
            return name, float(row[1])
        except (TypeError, ValueError):
            return None

    first = _pair(tops[0])
    if first is None:
        return ""
    span = f"最近 {int(days)} 天" if days else "这段时间"
    lines = [f"{span}，他最常用 {first[0]}（约 {_format_duration(first[1])}）"]

    second = _pair(tops[1]) if len(tops) > 1 else None
    if second is not None:
        total = float(payload.get("total_seconds") or 0.0)
        if total > 0 and second[1] >= total * PROFILE_SECONDARY_RATIO:
            lines[0] += f"，其次是 {second[0]}（约 {_format_duration(second[1])}）"

    peak = payload.get("peak_window")
    if isinstance(peak, (list, tuple)) and len(peak) == 2:
        try:
            lines.append(f"他通常在 {int(peak[0]):02d}:00–{int(peak[1]):02d}:00 这段时间用电脑")
        except (TypeError, ValueError):
            pass

    if payload.get("night_owl"):
        lines.append("深夜（0–6 点）他也常在电脑前")

    return "\n".join(lines)


def _render_battery(context: DesktopContext) -> str:
    """电量只在**低电量且未充电**时提。

    充电中（无论多少电）与电量充足都是噪音：用户要么已经在处理，
    要么根本不需要知道——感知层每多说一句，都在挤占真正该被看见的信息。
    """
    percent = context.battery_percent
    if percent is None:
        return ""
    if context.battery_charging is not False:
        # True = 正在充电；None = 设备没报（台式机 / 读不到电池）——都不提
        return ""
    level = int(round(percent))
    if level > 20:
        return ""
    return f"设备电量 {level}%，未充电"


def _render_idle(context: DesktopContext) -> str:
    if context.idle_seconds < IDLE_MENTION_SECONDS:
        return ""
    minutes = int(context.idle_seconds // 60)
    if minutes >= 60:
        return f"用户已经 {minutes // 60} 小时 {minutes % 60} 分钟没有操作电脑"
    return f"用户已经 {minutes} 分钟没有操作电脑"


def _render_window(context: DesktopContext) -> str:
    title = (context.foreground_title or "").strip()
    process = (context.foreground_process or "").strip()
    if title:
        return f"用户当前打开着「{title}」"
    if process:
        return f"用户当前在用 {process}"
    return ""


def _render_image(payload: dict) -> str:
    """用户分享的图片 → 一句中性事实（描述由 VLM 产出，见 `vision/`）。

    刻意**不写「用户想让你看图」**这类指令：感知层只陈述事实，
    「该怎么回应」是人设的事。写成指令就等于把人设写死在了感知层。
    """
    description = str(payload.get("description") or "").strip()
    if not description:
        return ""
    # 描述本身可能多行（VLM 偶尔会分行）——压成一行，保证每条事实只占一行
    flattened = " ".join(description.split())
    return f"用户分享了一张图片，画面里是：{flattened}"


# =============================================================
# 渲染入口
# =============================================================


def render_perception(
    events: list[PerceptionEvent],
    *,
    include_window_title: bool = False,
) -> str:
    """把感知事实渲染成提示词片段（每行一条 `- …`）。无可用事实时返回空串。

    参数:
        include_window_title: 是否把**前台窗口标题**渲染进去。默认 False——
            歌名是用户主动播放的、可以聊；窗口标题可能是一封邮件或一个病历页面。
            开关见 `Settings.perception_include_window_title`。
            **行踪与画像不受它管辖**：那两者渲染的是进程名（类别），不是标题。
    """
    lines: list[str] = []
    for event in events:
        if event.source == "clock":
            line = _render_clock(event.payload)
        elif event.kind == "now_playing":
            line = _render_now_playing(event.payload)
        elif event.kind == "user_image":
            line = _render_image(event.payload)
        elif event.source == "activity":
            line = render_activity_fact(event.payload)
        elif event.source == "profile":
            line = render_profile_facts(event.payload)
        elif event.source == "desktop" and event.kind == DESKTOP_KIND:
            # 桌面情景整体上报：一次产出多条事实，按信息量排序。
            # **不再渲染 local_time**——时间已由 clock 事实统一提供
            # （见 `clock.py`：那个来源 Web 端也有、且带日期与星期）。
            # 客户端上报的 local_time 仍然有用：`clock.time_fact` 用它取
            # 「用户墙上的钟」，容器部署时服务端可能在别的时区。
            try:
                context = DesktopContext.model_validate(event.payload)
            except Exception:  # noqa: BLE001 - 坏数据跳过，不阻断对话
                continue
            candidates = [
                _render_idle(context),
                _render_battery(context),
                _render_window(context) if include_window_title else "",
            ]
            lines.extend(item for item in candidates if item)
            continue
        elif event.kind == "user_speech":
            # 语音转写本身已经作为 user_input 进了对话，不再重复注入
            continue
        else:
            continue
        if line:
            # 画像一次产出多行（常用应用 / 时段 / 夜猫子），逐行加前缀
            lines.extend(line.splitlines())

    if not lines:
        return ""
    return "\n".join(f"- {line}" for line in lines)


def summarize(events: list[PerceptionEvent]) -> str:
    """一行短摘要（给界面徽标用；不是给模型的）。

    优先级：正在听什么 > 当前窗口 > 现在几点。
    **与事件顺序无关**地取优先级最高的那一条——汇集层按「从最当下到最长期」
    排序，而徽标要的是「最能说明此刻在干什么」的那条，两者不是一回事。
    """
    song = ""
    window = ""
    clock = ""
    for event in events:
        if not song and event.kind == "now_playing":
            song = _render_now_playing(event.payload)
        elif not clock and event.source == "clock":
            clock = _render_clock(event.payload)
        elif not window and event.source == "desktop" and event.kind == DESKTOP_KIND:
            try:
                window = _render_window(DesktopContext.model_validate(event.payload))
            except Exception:  # noqa: BLE001 - 坏数据当没有
                window = ""
    return song or window or clock


def render_current(*, now: datetime | None = None, settings=None) -> str:
    """**消费方唯一入口**：汇集当前全部感知事实并渲染。

    把「汇集」与「渲染」绑在一起，是因为拆开调用时极易漏掉其中一步——
    `render_perception([])` 是合法的空串，忘了先汇集就会静默地什么都不注入。
    """
    from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环
    from app.perception.gather import current_events  # noqa: PLC0415

    resolved = settings if settings is not None else get_settings()
    return render_perception(
        current_events(now=now, settings=resolved),
        include_window_title=resolved.perception_include_window_title,
    )


def render_activity_lines(segments, *, limit: int = 6) -> list[str]:
    """行踪段 → **给界面看的**短行（`21:03–21:40 Code.exe`）。

    与提示词渲染（`_render_activity`）刻意分开：界面上要的是「我到底被记了什么」，
    需要逐段、带时刻、可核对；提示词里要的是「一句话说得清的由头」。
    两者共用同一份数据，但**不是同一种文本**——硬凑成一个函数只会两头都不好用。
    """
    lines: list[str] = []
    for segment in list(segments)[-max(1, limit) :]:
        try:
            start = segment.start.strftime("%H:%M")
            end = "现在" if segment.ongoing else segment.end.strftime("%H:%M")
        except Exception:  # noqa: BLE001 - 时间戳坏了就跳过这一条
            continue
        name = str(getattr(segment, "process", "") or "").strip()
        if not name:
            continue
        lines.append(f"{start}–{end} {name}")
    return lines
