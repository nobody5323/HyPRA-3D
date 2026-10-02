"""主动链路的输入/输出模型。

刻意用**不可变 dataclass**（`frozen=True`）而不是可变对象：
触发器的 `evaluate` 是纯函数（只读上下文、返回意图或 None），
把上下文冻结掉，就不可能出现「某个触发器顺手改了上下文、影响后面的触发器」。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from app.memory.cold.models import Fact
from app.perception.models import DesktopContext, PerceptionEvent
from app.perception.timeline import ActivitySummary


@dataclass(frozen=True)
class TriggerContext:
    """一次评估所需的全部输入。"""

    now: datetime
    persona_id: str
    #: 用户最后一次说话的时刻（由对话路由记录，见 `api/chat.py`）
    last_user_message_at: datetime | None = None
    #: 上一次主动开口的时刻
    last_proactive_at: datetime | None = None
    #: 触发器去重表：`dedupe_key` → 上次触发的 ISO 时刻。
    #: 由触发器**自己**决定窗口（「今天已问候过」与「这首歌已经聊过」的窗口不同），
    #: 因此这里只提供原始记录，不做统一判据。
    fired: Mapping[str, str] = field(default_factory=dict)
    #: 桌面情景（Web 端永远为 None）
    desktop: DesktopContext | None = None
    #: 当前未过期的感知事实
    perception: tuple[PerceptionEvent, ...] = ()
    #: 该陪伴对象**进行中**且仍活跃的事实，按「最久没被提起」升序。
    #:
    #: 由 runner 从记忆门面取好再放进上下文——触发器因此仍然是**纯函数**
    #: （只读 ctx、不做 IO），可以逐条单测。见 `MemoryFollowUpTrigger`。
    ongoing_facts: tuple[Fact, ...] = ()
    #: 行踪摘要（最近几小时的段 + 「刚回来」的离开时长）。
    #:
    #: 同样是 runner 查好再放进来。没有行踪（Web 端 / 关了开关 / 刚装好还没数据）
    #: 时为 None，相关触发器静默失效。
    activity: ActivitySummary | None = None
    #: 行踪**已渲染**的一句事实（如「最近 3 小时，他用得最多的是 Code.exe（约 1 小时）」）。
    #:
    #: 为什么既给结构化的 `activity` 又给渲染好的字符串：判据要用结构
    #: （类别、时长），而写进意图 `facts` 要用句子。渲染复用
    #: `perception/context.py` 的同一套函数——触发器里**不写中文渲染**，
    #: 否则「注入提示词的措辞」与「触发原因里的措辞」迟早各写一份。
    activity_fact: str = ""
    #: 偏好画像**已渲染**的几行事实（常用应用 / 活跃时段 / 夜猫子）。
    profile_facts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProactiveIntent:
    """一个「想说点什么」的意图——**不是文案**。"""

    trigger_id: str
    #: 给模型看的触发原因（内部信息，不进用户可见文本）。
    #: 它同时是**记忆召回的 query**：主动开口必须「有由头」，
    #: 否则就是一句空泛的问候（见 §5.4）。
    reason: str
    #: 紧急/重要程度 0-1，影响闸门豁免与文案长度
    urgency: float = 0.5
    #: 关怀型（深夜、低电量、久未联系）：**可豁免免打扰时段**。
    #: 这个区分很关键——免打扰的目的是「别吵醒睡着的人」，
    #: 不是「看着你熬夜也不说话」。
    care: bool = False
    #: 去重键。为空则不记录（每次评估都可能重复触发）。
    dedupe_key: str = ""
    #: 附加给模型的事实（如歌名），会渲染进「主动开口」块
    facts: tuple[str, ...] = ()
