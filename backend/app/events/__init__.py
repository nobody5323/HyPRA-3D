"""事件推送层：把「后端主动发起的事情」送到前端。

设计见 `docs/proactive-multimodal.md` §5.1。与 `app/memory` / `app/tools` 同级，
是被主动链路与感知链路**共用**的传输层——两者都只负责「产出事件」，不关心怎么送。
"""

from app.events.bus import Event, EventBus, get_event_bus, set_event_bus
from app.events.types import (
    EVENT_PERCEPTION_HINT,
    EVENT_PROACTIVE_MESSAGE,
    EVENT_PROACTIVE_SKIPPED,
    event_scope,
)

__all__ = [
    "Event",
    "EventBus",
    "get_event_bus",
    "set_event_bus",
    "event_scope",
    "EVENT_PROACTIVE_MESSAGE",
    "EVENT_PROACTIVE_SKIPPED",
    "EVENT_PERCEPTION_HINT",
]
