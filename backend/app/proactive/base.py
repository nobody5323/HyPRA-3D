"""触发器接口。

一个触发器只回答一个问题：**「现在有没有理由开口？」**
- 有 → 返回一个 `ProactiveIntent`（意图，不是文案）；
- 没有 → 返回 None。

它**不做**的事（都很重要）：
- 不判断「该不该说」（那是闸门的事，见 `gate.py`）；
- 不生成文案（那是对话链路的事，见 `runner.py`）；
- 不推送（那是事件总线的事）。

职责这么切，是因为这三件事的变更频率完全不同：加一种「由头」很常见，
改节制规则次之，换生成方式几乎不会。混在一起就会「加个触发器顺手改到节制」。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.proactive.models import ProactiveIntent, TriggerContext


class ProactiveTrigger(ABC):
    """主动开口的「由头」来源。"""

    #: 触发器 id（出现在 `proactive_skipped` 事件与状态文件里，用于排查）
    id: str = ""

    @abstractmethod
    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        """评估是否该开口。必须是**纯函数**（只读 ctx，不做 IO）。"""

    def describe(self) -> dict:
        """给界面/调试用的自述。"""
        return {"id": self.id, "type": type(self).__name__}
