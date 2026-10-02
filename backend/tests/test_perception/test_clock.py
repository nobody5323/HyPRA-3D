"""时钟事实单测（`app/perception/clock.py`）。

要钉住的两条：
1. **客户端在线时用客户端的钟**（容器部署下服务端可能在别的时区）；
2. **拿不到客户端时间时回落服务端**，且格式与前者**逐字一致**——
   「桌宠端看到的」与「Web 端看到的」不能是两套措辞。
"""

from datetime import datetime, timedelta, timezone

from app.perception.clock import CLOCK_KIND, CLOCK_SOURCE, time_fact
from app.perception.context import render_perception
from app.perception.models import DesktopContext

TZ = timezone(timedelta(hours=8))


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, tzinfo=TZ)


class TestTimeFact:
    def test_uses_server_time_when_no_client(self) -> None:
        event = time_fact(now=_at(22, 13))
        assert event.source == CLOCK_SOURCE
        assert event.kind == CLOCK_KIND
        assert event.payload["origin"] == "server"
        assert event.payload["date"] == "2026-09-29"
        assert event.payload["hour"] == 22
        assert event.payload["minute"] == 13
        assert event.payload["weekday"] == 1  # 2026-09-29 是周二

    def test_client_time_wins_when_available(self) -> None:
        """客户端的钟才是用户墙上的钟——容器里的服务端可能是 UTC。"""
        desktop = DesktopContext(local_date="2026-09-29", local_time="07:05")
        event = time_fact(desktop=desktop, now=_at(23, 59))
        assert event.payload["origin"] == "client"
        assert event.payload["hour"] == 7
        assert event.payload["minute"] == 5

    def test_falls_back_when_client_time_missing(self) -> None:
        event = time_fact(desktop=DesktopContext(local_time=""), now=_at(22, 13))
        assert event.payload["origin"] == "server"

    def test_falls_back_when_client_time_malformed(self) -> None:
        """客户端填了乱值也不该让「现在几点」整条消失——宁可显示服务端的钟。"""
        desktop = DesktopContext(local_date="2026-09-29", local_time="下午三点")
        event = time_fact(desktop=desktop, now=_at(22, 13))
        assert event.payload["origin"] == "server"
        assert event.payload["hour"] == 22

    def test_client_date_defaults_to_server_date(self) -> None:
        """只给了时刻没给日期时，用服务端的日期补上（不能整条丢掉）。"""
        event = time_fact(desktop=DesktopContext(local_time="07:05"), now=_at(22, 13))
        assert event.payload["origin"] == "client"
        assert event.payload["date"] == "2026-09-29"
        assert event.payload["hour"] == 7

    def test_payload_has_no_chinese(self) -> None:
        """中性字段纪律：payload 里不含任何中文句子（渲染是 context.py 的事）。"""
        payload = time_fact(now=_at(22, 13)).payload
        assert all(isinstance(value, (str, int, float)) for value in payload.values())
        assert not any(
            isinstance(value, str) and any("\u4e00" <= ch <= "\u9fff" for ch in value)
            for value in payload.values()
        )


def test_renders_the_same_shape_for_both_origins() -> None:
    """两种来源渲染出来的**句式**必须一致（只差具体时刻）。"""
    server = render_perception([time_fact(now=_at(22, 13))])
    client = render_perception(
        [time_fact(desktop=DesktopContext(local_date="2026-09-29", local_time="22:13"))]
    )
    assert server == client == "- 现在是 9 月 29 日 星期二 22:13，晚上"
