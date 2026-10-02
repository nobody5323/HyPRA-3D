"""主动链路：触发 → 节制 → 复用对话图 → 推送。

设计见 `docs/proactive-multimodal.md` §5。**两条硬约定**：

1. **触发器只产出「意图」，不产出文案**（§5.2）。文案一律由对话链路生成——
   触发器自己拼一句话，就是第二套人格，迟早和主链路漂移。
2. **主动消息必须走同一条 `chat_graph`**（§5.4）。另写一套生成 = 人格漂移 + 两处维护。

链路形态::

    Trigger（定时 / 间隔 / 情景 / 记忆到期）
        ▼
    Gate 节制层（七道闸门，纯函数）──skip──▶ proactive_skipped 事件
        ▼
    Intent（意图，不是文案）
        ▼
    合成内部消息 ──▶ 复用 chat_graph（proactive=true）
        ▼
    EventBus ──SSE──▶ 前端（数字人开口 + 气泡 + 通知）
"""

from app.proactive.base import ProactiveTrigger
from app.proactive.gate import GateContext, GateDecision, evaluate_gate, in_quiet_hours
from app.proactive.models import ProactiveIntent, TriggerContext
from app.proactive.runner import ProactiveRunner, get_proactive_runner, set_proactive_runner
from app.proactive.state import ProactiveState, ProactiveStateStore

__all__ = [
    "GateContext",
    "GateDecision",
    "ProactiveIntent",
    "ProactiveRunner",
    "ProactiveState",
    "ProactiveStateStore",
    "ProactiveTrigger",
    "TriggerContext",
    "evaluate_gate",
    "get_proactive_runner",
    "in_quiet_hours",
    "set_proactive_runner",
]
