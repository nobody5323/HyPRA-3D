"""感知快照：进程内的「此刻」状态（带 TTL）。

## 为什么是快照，不是队列

消费方要回答的是「**现在**是什么情况」，而不是「刚才发生过什么」。所以：
- 同一 `(source, kind)` 的新观测**覆盖**旧观测（不堆积）；
- 读的时候按 TTL 过滤（过期即不可见，不返回陈旧事实）。

这与事件总线（`app/events/bus.py`）方向相反、互不替代：
总线是「把一件事推给界面」，快照是「问现在怎么样」。

## 为什么带锁

写入来自 API 路由（事件循环线程），读取来自对话图的执行线程
（`POST /chat` 把图丢进线程池，见 `api/chat.py` ③）。
两边跨线程访问同一个字典，不加锁会出现「读到半个字典」这种偶发问题。
读多写少，`threading.Lock` 的开销可以忽略。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app.perception.models import DesktopContext, PerceptionEvent

logger = logging.getLogger(__name__)

#: 桌面情景的整体 TTL。取 120s：客户端每 30s 左右上报一次，
#: 留 4 个周期的容忍度——用户短暂切到全屏游戏（上报被暂停）时，
#: 不该立刻把「刚才在听歌」当成过期。
DESKTOP_TTL_SECONDS = 120

#: 快照最多保留多少条不同事实（防某个源不断造出新 kind 把内存撑大）
MAX_EVENTS = 32

#: 桌面情景在快照里的固定 kind
DESKTOP_KIND = "desktop_context"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PerceptionSnapshot:
    """进程内感知快照。"""

    def __init__(self, *, max_events: int = MAX_EVENTS) -> None:
        self.max_events = max_events
        self._events: dict[tuple[str, str], PerceptionEvent] = {}
        self._lock = threading.Lock()

    # ---------- 写入 ----------

    def record(self, event: PerceptionEvent) -> None:
        """记录一条事实（同 source+kind 覆盖旧值）。"""
        with self._lock:
            self._events[(event.source, event.kind)] = event
            if len(self._events) > self.max_events:
                # 超出上限时丢最旧的一条（按观测时刻）
                oldest = min(
                    self._events.items(), key=lambda kv: kv[1].observed_at
                )[0]
                self._events.pop(oldest, None)

    def record_desktop(self, context: DesktopContext) -> PerceptionEvent:
        """记录一次桌面情景上报（整体覆盖）。

        顺带把**正在播放**单独记成一条 `now_playing` 事实：歌曲是「一个可聊的由头」，
        与「电量 / 时段」这类背景信息**消费方式完全不同**——
        前者会被情景触发器读来判断「要不要聊两句」，后者只进提示词。
        拆成两条事实，触发器就不必去解包整个桌面情景，渲染也不必特判。

        歌停了一并清掉：`title` 变空说明用户暂停/关闭了播放器，
        继续留着会让模型以为「他还在听那首歌」。
        """
        event = PerceptionEvent(
            source="desktop",
            kind=DESKTOP_KIND,
            payload=context.model_dump(),
            ttl_seconds=DESKTOP_TTL_SECONDS,
        )
        self.record(event)

        title = (context.now_playing_title or "").strip()
        if title:
            self.record(
                PerceptionEvent(
                    source="desktop",
                    kind="now_playing",
                    payload={
                        "title": title,
                        "artist": (context.now_playing_artist or "").strip(),
                    },
                    ttl_seconds=DESKTOP_TTL_SECONDS,
                )
            )
        else:
            with self._lock:
                self._events.pop(("desktop", "now_playing"), None)
        return event

    # ---------- 读取 ----------

    def current(self, *, now: datetime | None = None) -> list[PerceptionEvent]:
        """当前未过期的事实（按观测时刻排序，最新的在后）。"""
        moment = now or _now()
        with self._lock:
            alive = [e for e in self._events.values() if not e.expired(now=moment)]
        return sorted(alive, key=lambda e: e.observed_at)

    def get(self, source: str, kind: str, *, now: datetime | None = None) -> PerceptionEvent | None:
        """取某一条事实；已过期则返回 None。"""
        with self._lock:
            event = self._events.get((source, kind))
        if event is None or event.expired(now=now or _now()):
            return None
        return event

    def desktop(self, *, now: datetime | None = None) -> DesktopContext | None:
        """当前桌面情景；没有或已过期则返回 None（Web 端永远拿不到）。"""
        event = self.get("desktop", DESKTOP_KIND, now=now)
        if event is None:
            return None
        try:
            return DesktopContext.model_validate(event.payload)
        except Exception as exc:  # noqa: BLE001 - 坏数据不该让对话起不来
            logger.warning("桌面情景数据无法解析，已忽略：%s", exc)
            return None

    # ---------- 清理 ----------

    def clear(self, *, source: str | None = None) -> int:
        """清空（可按源）。返回清掉的条数。

        **关闭开关时调用**：`§4.6` 的红线是「关掉开关后采集链路立即停止」，
        不是「采集了不用」——所以关闭动作必须连带清掉已有快照。
        """
        with self._lock:
            if source is None:
                count = len(self._events)
                self._events.clear()
                return count
            keys = [k for k in self._events if k[0] == source]
            for key in keys:
                self._events.pop(key, None)
            return len(keys)

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


#: 进程内单例
_snapshot: PerceptionSnapshot | None = None


def get_perception_snapshot() -> PerceptionSnapshot:
    """取感知快照单例。"""
    global _snapshot
    if _snapshot is None:
        _snapshot = PerceptionSnapshot()
    return _snapshot


def set_perception_snapshot(snapshot: PerceptionSnapshot | None) -> None:
    """替换/重置感知快照（测试注入用）。"""
    global _snapshot
    _snapshot = snapshot
