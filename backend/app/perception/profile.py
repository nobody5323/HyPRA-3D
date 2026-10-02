"""偏好画像：从行踪里聚合出「他平时是什么样的人」。

## 为什么是确定性聚合，不是让模型总结

最直觉的做法是把七天的行踪丢给模型，让它写一段「他是个夜猫子，喜欢写代码」。
本模块**刻意不这么做**，三条理由：

| 理由 | 说明 |
| --- | --- |
| **可解释** | 「他最常用 Code.exe（12 小时）」这句话能追到每一段行踪。模型总结出来的「他热爱编程」追不到任何东西，错了也查不出来 |
| **零成本** | 画像每轮对话都要注入。走模型等于每轮多一次调用，而它只是几十条秒数的加法 |
| **不越界** | 模型爱做价值判断（「他工作压力很大」）。行踪只支持「他在用 XX」，不支持「他是个 XX 的人」——后者是人设该说的话，不是感知层 |

因此这里只产出**计数与排序**，措辞交给 `context.py`，解读交给 persona。

## 「偏好」有两半，这里只做行为的那一半

- **说出来的偏好**（「我喜欢下雨天」）已经由冷层语义记忆（`FactType.PREFERENCE`）
  负责，且已经常驻注入；
- **做出来的偏好**（每天在用什么）此前**完全没有**——本模块补的是这一半。

两半不重叠、不互相替代：一个是从对话里听见的，一个是从行为里看到的。

## 数据不进记忆库

画像只是「感知的聚合形态」，生命周期跟着行踪走（保留 7 天）。
按 `docs/proactive-multimodal.md` §4.6，感知数据**不进记忆库**——
否则会出现「角色记得你上个月一直在用某个软件」这种越界。

## 缓存

聚合要读 7 个日文件。虽然量很小（每天几十行），但**每轮对话都算一遍**没有意义
——画像的粒度本来就是「天」。因此带一个 5 分钟的内存缓存，键是「窗口天数」。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.perception.clock import local_now
from app.perception.timeline import ActivitySegment, get_timeline

logger = logging.getLogger(__name__)

#: 画像默认回看天数
DEFAULT_PROFILE_DAYS = 7

#: 产出画像所需的最小累计时长（秒）。
#: 低于它就不出画像——用两分钟的行踪去说「他最常用 Code.exe」是不诚实的，
#: 那只是「他刚打开了 Code」。默认 30 分钟。
DEFAULT_MIN_SECONDS = 30 * 60.0

#: 「活跃时段」的判定门槛（秒）：某个整点累计超过它才算「他常在这个点用电脑」
DEFAULT_ACTIVE_HOUR_SECONDS = 15 * 60.0

#: 峰值时段窗口宽度（小时）
PEAK_WINDOW_HOURS = 3

#: 深夜定义（用于「夜猫子」判据）：0:00–05:59
NIGHT_HOURS = frozenset({0, 1, 2, 3, 4, 5})

#: 深夜占比达到它才算「常在深夜用电脑」
NIGHT_OWL_RATIO = 0.12

#: 缓存有效期（秒）
CACHE_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class ActivityProfile:
    """一段时间的行踪聚合结果（全是机器可读字段）。"""

    days: int
    #: 参与聚合的总时长（秒）
    total_seconds: float
    #: (进程名, 秒) 按累计时长降序
    top_processes: tuple[tuple[str, float], ...] = ()
    #: (类别键, 秒) 按累计时长降序
    top_categories: tuple[tuple[str, float], ...] = ()
    #: 常活跃的整点（升序，如 (9, 10, 14, 20, 21, 22)）
    active_hours: tuple[int, ...] = ()
    #: 峰值时段（起止整点，含起不含止），如 (20, 23)
    peak_window: tuple[int, int] | None = None
    #: 深夜使用占比 0-1
    night_ratio: float = 0.0

    @property
    def usable(self) -> bool:
        """样本够不够支撑一句话。"""
        return self.total_seconds > 0 and bool(self.top_processes)

    @property
    def night_owl(self) -> bool:
        return self.night_ratio >= NIGHT_OWL_RATIO


def _hour_buckets(
    start: datetime, end: datetime
) -> dict[int, float]:
    """把 `[start, end)` 的时长摊到它跨过的每个整点上。

    为什么按小时切而不是「按开始时刻归一类」：一段可能从 22:50 跨到 00:20，
    只按开始时刻记会把 20 分钟记进 22 点、0 点完全没数据——「夜猫子」这个判据
    恰恰就靠这些跨零点的段。
    """
    if end <= start:
        return {}
    buckets: dict[int, float] = {}
    cursor = start
    while cursor < end:
        hour_end = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        chunk_end = min(hour_end, end)
        buckets[cursor.hour] = buckets.get(cursor.hour, 0.0) + (chunk_end - cursor).total_seconds()
        cursor = chunk_end
    return buckets


def _peak_window(hour_seconds: dict[int, float]) -> tuple[int, int] | None:
    """找累计时长最高的连续 `PEAK_WINDOW_HOURS` 小时（**允许跨零点**）。

    跨零点必须支持：夜猫子的峰值是 22:00–01:00，写成 22:00–22:00 就没了意义。
    """
    if not hour_seconds:
        return None
    best: tuple[float, int] | None = None
    for start in range(24):
        total = sum(hour_seconds.get((start + offset) % 24, 0.0) for offset in range(PEAK_WINDOW_HOURS))
        if best is None or total > best[0]:
            best = (total, start)
    if best is None or best[0] <= 0:
        return None
    return (best[1], (best[1] + PEAK_WINDOW_HOURS) % 24)


def build_profile(
    segments: list[ActivitySegment],
    *,
    days: int = DEFAULT_PROFILE_DAYS,
    now: datetime | None = None,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    active_hour_seconds: float = DEFAULT_ACTIVE_HOUR_SECONDS,
    top_n: int = 3,
) -> ActivityProfile:
    """把行踪段聚合成画像（**纯函数**：输入段、输出画像，可逐条单测）。"""
    moment = now or local_now()
    window_start = moment - timedelta(days=days)
    process_seconds: dict[str, float] = {}
    category_seconds: dict[str, float] = {}
    hour_seconds: dict[int, float] = {}
    total = 0.0

    for segment in segments:
        if not segment.process:
            continue
        # 只统计落在窗口内的部分：一段可能从 6 天前的晚上跨到现在，
        # 也可能仍在进行（用 now 收尾）。先求交集，再做后续一切统计，
        # 否则「总时长」与「小时分布」会用两个不同的区间，数字对不上。
        start = max(segment.start, window_start)
        end = min(moment if segment.ongoing else segment.end, moment)
        if end <= start:
            continue
        duration = (end - start).total_seconds()
        if duration <= 0:
            continue

        total += duration
        process_seconds[segment.process] = process_seconds.get(segment.process, 0.0) + duration
        category_seconds[segment.category] = category_seconds.get(segment.category, 0.0) + duration
        for hour, seconds in _hour_buckets(start, end).items():
            hour_seconds[hour] = hour_seconds.get(hour, 0.0) + seconds

    if total < min_seconds:
        # 样本不足：只回总量，不出任何结论。**宁可不说，也不要说错**。
        return ActivityProfile(days=days, total_seconds=total)

    ranked = sorted(process_seconds.items(), key=lambda item: (-item[1], item[0]))
    categories = sorted(category_seconds.items(), key=lambda item: (-item[1], item[0]))
    active = tuple(sorted(hour for hour, seconds in hour_seconds.items() if seconds >= active_hour_seconds))
    night = sum(seconds for hour, seconds in hour_seconds.items() if hour in NIGHT_HOURS)

    return ActivityProfile(
        days=days,
        total_seconds=total,
        top_processes=tuple(ranked[:top_n]),
        top_categories=tuple(categories[:top_n]),
        active_hours=active,
        peak_window=_peak_window(hour_seconds),
        night_ratio=(night / total) if total > 0 else 0.0,
    )


# =============================================================
# 带缓存的取用入口
# =============================================================


class ProfileCache:
    """画像的按天缓存（进程内）。

    为什么需要缓存而不是每轮现算：聚合本身不贵，但**每轮对话都做一次
    没有意义的重复劳动**——画像的粒度是天，5 分钟内的结果不会变。
    缓存也让「一轮对话里三处都问画像」只付一次代价。
    """

    def __init__(self, *, ttl_seconds: float = CACHE_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._value: ActivityProfile | None = None
        self._at: datetime | None = None
        self._signature: tuple = ()

    def get(
        self,
        *,
        days: int,
        signature: tuple = (),
        now: datetime | None = None,
        settings=None,
    ) -> ActivityProfile:
        """取画像；过期或参数变了就重算。"""
        moment = now or local_now()
        key = (days, signature)
        with self._lock:
            fresh = (
                self._value is not None
                and self._at is not None
                and key == self._signature
                and (moment - self._at).total_seconds() < self.ttl_seconds
            )
            if fresh:
                return self._value  # type: ignore[return-value]
        profile = self._compute(days=days, now=moment, settings=settings)
        with self._lock:
            self._value = profile
            self._at = moment
            self._signature = key
        return profile

    def _compute(self, *, days: int, now: datetime, settings=None) -> ActivityProfile:
        """读行踪并聚合。任何异常都退化成「空画像」——画像不可用不该让对话失败。"""
        try:
            from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

            resolved = settings if settings is not None else get_settings()
            timeline = get_timeline(settings=resolved)
            segments = timeline.segments(hours=days * 24.0, now=now)
            return build_profile(
                segments,
                days=days,
                now=now,
                min_seconds=float(resolved.perception_profile_min_minutes) * 60.0,
            )
        except Exception as exc:  # noqa: BLE001 - 见 docstring
            logger.warning("偏好画像聚合失败（本轮不注入）：%s", exc)
            return ActivityProfile(days=days, total_seconds=0.0)

    def reset(self) -> None:
        with self._lock:
            self._value = None
            self._at = None
            self._signature = ()


#: 进程内单例
_cache = ProfileCache()


def get_profile(
    *,
    days: int = DEFAULT_PROFILE_DAYS,
    now: datetime | None = None,
    settings=None,
) -> ActivityProfile:
    """取当前偏好画像（带缓存）。

    `settings` 由调用方透传（不传就取全局）：主动链路用的是自己那份 Settings，
    而画像要读的行踪、最小样本量都来自它——两处各取一次全局配置的话，
    在测试里注入的 Settings 就会被绕过。
    """
    from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

    resolved = settings if settings is not None else get_settings()
    signature = (
        str(resolved.perception_activity_include_title),
        str(resolved.perception_profile_min_minutes),
    )
    return _cache.get(days=days, signature=signature, now=now, settings=resolved)


def reset_profile_cache() -> None:
    """清空画像缓存（测试 / 清除行踪后调用）。"""
    _cache.reset()
