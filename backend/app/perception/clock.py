"""时间感：让角色知道「现在是什么时候」。

## 为什么时间不能只从桌面情景里来

原先「现在是几点」只存在于 `DesktopContext.local_time` 里，而那是桌宠端
**上报**来的——要同时满足「桌宠窗开着」与「上报没过期（TTL 120s）」两个条件。
于是：

- Web 端**永远没有时间**（它拿不到桌面情景）；
- 桌宠端在窗口被关掉、暂停上报、或用户在全屏应用里（上报被跳过）时也会丢；
- 即便拿到了，也只有 `HH:MM`——没有日期、没有星期、没有「深夜还是上午」。

而「今天星期几、现在几点」是对话里最基础的一条上下文。模型不知道时间，
就会在凌晨两点说「今天过得怎么样呀」——这不是人设的问题，是它真的不知道。

## 两条来源，一个格式

容器化部署（docker compose）时后端可能在 UTC，而用户在 UTC+8。因此：

| 情形 | 用谁的时间 |
| --- | --- |
| 客户端上报了 `local_time`（桌宠端在线） | **客户端的**——那才是用户墙上的钟 |
| 没有客户端上报（Web 端 / 桌宠窗关着） | **服务端的本地时间** |

两条来源都归一成同一组中性字段（`date` / `hour` / `minute` / `weekday` / `origin`），
中文句子由 `context.py` 唯一渲染——所以「桌宠端看到的格式」与
「Web 端看到的格式」逐字一致，不会出现两套措辞。
"""

from __future__ import annotations

from datetime import datetime

from app.perception.models import DesktopContext, PerceptionEvent

#: 时钟事实的 source / kind
CLOCK_SOURCE = "clock"
CLOCK_KIND = "local_time"

#: 存活秒数。时钟每次都是**现算**的（见 `gather.py`），这个值只影响
#: 万一被放进快照时的过期判定，取 60s 即可。
CLOCK_TTL_SECONDS = 60

#: 星期名（`datetime.weekday()`：0 = 周一）
WEEKDAY_NAMES: tuple[str, ...] = ("一", "二", "三", "四", "五", "六", "日")


def local_now() -> datetime:
    """本地时区当前时刻（带 tzinfo）。

    **必须用本地时区**：免打扰时段（23:00–08:00）是用户墙上的钟，
    用 UTC 会让「深夜别说话」在 UTC+8 变成「下午别说话」。
    """
    return datetime.now().astimezone()


def ensure_aware(value: datetime) -> datetime:
    """把可能不带时区的时间补成**本地时区**（已是 aware 则原样返回）。

    为什么需要：冷层 `Fact.created_at` 用的是 `datetime.now()`（**naive**），
    而触发上下文里的 `now` 是 aware 的——两者相减会直接抛
    `TypeError: can't subtract offset-naive and offset-aware datetimes`。
    那是个只在「记忆里有进行中事项」时才会炸的错，而它会把整轮主动评估吞掉。

    补本地时区（而不是 UTC）：这些时间本来就是本机时钟写下的，
    按本地解读才与「现在几点」可比。
    """
    if value.tzinfo is not None:
        return value
    return value.astimezone()


def _client_moment(desktop: DesktopContext, *, fallback: datetime) -> datetime | None:
    """把客户端上报的 `local_date` + `local_time` 拼成一个时刻。

    拼不出来（缺 `local_time`、格式不对、日期是乱填的）就返回 None，
    由调用方回落到服务端时间——**宁可显示服务端的钟，也不能不显示时间**。
    """
    time_text = (desktop.local_time or "").strip()
    if not time_text:
        return None
    date_text = (desktop.local_date or "").strip() or fallback.strftime("%Y-%m-%d")
    try:
        parsed = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    # 客户端给的是「墙上的钟」，本身不带时区；按服务端本地时区解读。
    # 这样它与 `local_now()` 可比，且渲染时用的都是同一套字段。
    return parsed.astimezone()


def time_fact(
    *,
    desktop: DesktopContext | None = None,
    now: datetime | None = None,
) -> PerceptionEvent:
    """产出「现在是什么时候」这条中性事实。

    参数:
        desktop: 当前桌面情景（Web 端为 None）。有客户端时间时优先用它。
        now: 服务端当前时刻（测试注入用；默认 `local_now()`）。

    返回的事件 `payload` 只含机器可读字段，**不含任何中文**——
    渲染是 `context.py` 的事（见模块文档）。
    """
    moment = now or local_now()
    origin = "server"
    if desktop is not None:
        client = _client_moment(desktop, fallback=moment)
        if client is not None:
            moment = client
            origin = "client"

    return PerceptionEvent(
        source=CLOCK_SOURCE,
        kind=CLOCK_KIND,
        payload={
            "date": moment.strftime("%Y-%m-%d"),
            "hour": moment.hour,
            "minute": moment.minute,
            "weekday": moment.weekday(),
            "origin": origin,
        },
        observed_at=moment,
        ttl_seconds=CLOCK_TTL_SECONDS,
    )
