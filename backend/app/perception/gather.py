"""汇集「此刻」的全部感知事实：快照 + 时钟 + 行踪 + 画像。

## 为什么需要这一层

感知事实现在有**四个**来源，而消费方（对话路由 / 状态接口 / 主动链路）
只该看到一个入口：

| 来源 | 内容 | 在哪 |
| --- | --- | --- |
| 快照 `snapshot.py` | 歌 / 窗口 / 空闲 / 电量 / 图片 | 进程内，客户端上报驱动 |
| 时钟 `clock.py` | 现在几点、星期几、什么时段 | **每轮现算**，不进快照 |
| 行踪 `timeline.py` | 最近几小时在用什么 | 从落盘历史聚合 |
| 画像 `profile.py` | 最近几天最常用什么、什么时候活跃 | 从行踪聚合（带缓存） |

后三者**都不该进快照**：快照的语义是「客户端刚刚报了什么」（同 source+kind 覆盖、
带 TTL），而时钟是每次调用都不同的瞬时值，行踪与画像是有自己生命周期的聚合结果。
把它们塞进快照会污染那个语义。

但也不能让三处调用点各自去拼——那样会出现「对话里有时钟、状态接口里没有」
「主动开口看不到行踪」这类不一致，而且**加一个来源要改三处**。
所以汇集逻辑只有这一个函数。

## 顺序是有意的：从最当下到最长期

时钟 → 快照事实 → 行踪 → 画像。

模型读到的第一行是「现在几点」，最后一行是「他平时是怎样的人」——
注入位置越靠前的内容对生成的锚定越强，而「此刻」这一层里最该被锚定的是时间。

## 关了就是真的没有

四个开关各自独立：`perception_include_time` / `perception_activity_enabled` /
`perception_profile_enabled` / `perception_desktop_enabled`。
任一关掉，对应来源**连事件都不产出**（不是「产出了不渲染」）——
对齐 §4.6「关掉开关后采集链路立即停止」。

一个例外要单独说：**画像依赖行踪**。行踪关掉时画像也自动关掉，
因为画像没有别的数据来源（见 `_profile_enabled`）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.perception.clock import time_fact
from app.perception.models import PerceptionEvent
from app.perception.snapshot import get_perception_snapshot

logger = logging.getLogger(__name__)

#: 行踪事实的 source / kind
ACTIVITY_SOURCE = "activity"
ACTIVITY_KIND = "recent_activity"

#: 画像事实的 source / kind
PROFILE_SOURCE = "profile"
PROFILE_KIND = "activity_profile"

#: 行踪事实的存活秒数（TTL）。
#: 它由聚合现算（不是客户端上报），TTL 只影响万一被放进快照时的过期判定，
#: 取 120s 与桌面情景同口径即可。
ACTIVITY_TTL_SECONDS = 120
PROFILE_TTL_SECONDS = 600


def _activity_event(
    *, hours: float, now: datetime | None = None, settings=None
) -> PerceptionEvent | None:
    """把「最近几小时在做什么」变成一条中性事实（样本不足时返回 None）。"""
    from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

    from app.perception.timeline import get_timeline  # noqa: PLC0415

    resolved = settings if settings is not None else get_settings()
    timeline = get_timeline(settings=resolved)
    segments = timeline.segments(hours=hours, now=now)
    if not segments:
        return None

    rows = []
    for segment in segments[-6:]:  # 最多 6 段：再多也不是「最近在做什么」了
        rows.append(
            {
                "process": segment.process,
                "category": segment.category,
                "title": segment.title,
                "seconds": round(segment.seconds(now=now)),
                "ongoing": segment.ongoing,
            }
        )
    return PerceptionEvent(
        source=ACTIVITY_SOURCE,
        kind=ACTIVITY_KIND,
        payload={"hours": hours, "segments": rows},
        ttl_seconds=ACTIVITY_TTL_SECONDS,
    )


def _profile_event(*, now: datetime | None = None, settings=None) -> PerceptionEvent | None:
    """把偏好画像变成一条中性事实（样本不足时返回 None）。"""
    from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

    from app.perception.profile import get_profile  # noqa: PLC0415

    resolved = settings if settings is not None else get_settings()
    profile = get_profile(days=resolved.perception_profile_days, now=now, settings=resolved)
    if not profile.usable:
        return None

    return PerceptionEvent(
        source=PROFILE_SOURCE,
        kind=PROFILE_KIND,
        payload={
            "days": profile.days,
            "total_seconds": round(profile.total_seconds),
            "top_processes": [[name, round(seconds)] for name, seconds in profile.top_processes],
            "top_categories": [[name, round(seconds)] for name, seconds in profile.top_categories],
            "active_hours": list(profile.active_hours),
            "peak_window": list(profile.peak_window) if profile.peak_window else None,
            "night_ratio": round(profile.night_ratio, 3),
        },
        ttl_seconds=PROFILE_TTL_SECONDS,
    )


def _profile_enabled(settings) -> bool:
    """画像是否可用。

    **画像依赖行踪**：行踪关掉时画像也自动关掉。这不是「多一道保险」，
    而是语义上的依赖——画像**没有别的数据来源**。
    两个开关分开是为了让用户能「记行踪，但不要它总结」，
    而不是「关了行踪还能总结」（那等于开关是假的）。
    """
    return bool(settings.perception_profile_enabled and settings.perception_activity_enabled)


def current_events(*, now: datetime | None = None, settings=None) -> list[PerceptionEvent]:
    """汇集当前全部感知事实（已按「从最当下到最长期」排好序）。"""
    if settings is None:
        from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

        settings = get_settings()

    snapshot = get_perception_snapshot()
    events: list[PerceptionEvent] = []

    if settings.perception_include_time:
        # 时钟每轮现算：客户端在线时用它的墙上的钟，否则用服务端本地时间
        events.append(time_fact(desktop=snapshot.desktop(now=now), now=now))

    events.extend(snapshot.current(now=now))

    if settings.perception_activity_enabled:
        try:
            event = _activity_event(
                hours=settings.perception_activity_recent_hours, now=now, settings=settings
            )
        except Exception as exc:  # noqa: BLE001 - 一个来源坏了不该让整层空掉
            logger.warning("感知来源「行踪」不可用（已跳过）：%s", exc)
        else:
            if event is not None:
                events.append(event)

    if _profile_enabled(settings):
        try:
            event = _profile_event(now=now, settings=settings)
        except Exception as exc:  # noqa: BLE001 - 同上
            logger.warning("感知来源「画像」不可用（已跳过）：%s", exc)
        else:
            if event is not None:
                events.append(event)

    return events


# =============================================================
# 给主动链路用的取用入口
# =============================================================
#
# 触发器必须是**纯函数**（只读上下文、不做 IO，见 `proactive/models.py`），
# 所以行踪与画像要由 runner 先查好、再放进 `TriggerContext`。
# 这三个函数就是 runner 用的取用口——渲染复用 `context.py` 的同一套函数，
# 保证「注入提示词的措辞」与「触发原因里的措辞」不会各写一份。


def activity_summary(*, now: datetime | None = None, settings=None):
    """取给触发器用的行踪摘要；未开启行踪或没有数据时返回 None。"""
    resolved = settings if settings is not None else _settings()
    if not resolved.perception_activity_enabled:
        return None
    from app.perception.timeline import get_timeline  # noqa: PLC0415

    try:
        summary = get_timeline(settings=resolved).summary(
            hours=resolved.perception_activity_recent_hours, now=now
        )
    except Exception as exc:  # noqa: BLE001 - 行踪不可用不该让主动链路哑掉
        logger.warning("读取行踪摘要失败（本轮跳过）：%s", exc)
        return None
    return summary if summary.segments else None


def activity_fact(*, now: datetime | None = None, settings=None) -> str:
    """行踪 → 一句已渲染的事实（`最近 3 小时，他用得最多的是 …`）；没有则空串。"""
    resolved = settings if settings is not None else _settings()
    if not resolved.perception_activity_enabled:
        return ""
    from app.perception.context import render_activity_fact  # noqa: PLC0415

    try:
        event = _activity_event(
            hours=resolved.perception_activity_recent_hours, now=now, settings=resolved
        )
    except Exception as exc:  # noqa: BLE001 - 见上
        logger.warning("读取行踪失败（本轮跳过）：%s", exc)
        return ""
    return render_activity_fact(event.payload) if event is not None else ""


def profile_facts(*, now: datetime | None = None, settings=None) -> tuple[str, ...]:
    """偏好画像 → 已渲染的几行事实；样本不足或未开启时返回空元组。"""
    resolved = settings if settings is not None else _settings()
    if not _profile_enabled(resolved):
        return ()
    from app.perception.context import render_profile_facts  # noqa: PLC0415

    try:
        event = _profile_event(now=now, settings=resolved)
    except Exception as exc:  # noqa: BLE001 - 见上
        logger.warning("读取偏好画像失败（本轮跳过）：%s", exc)
        return ()
    if event is None:
        return ()
    rendered = render_profile_facts(event.payload)
    return tuple(rendered.splitlines()) if rendered else ()


def _settings():
    from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

    return get_settings()
