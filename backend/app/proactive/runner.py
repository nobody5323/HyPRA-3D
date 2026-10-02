"""主动链路调度器：评估 → 闸门 → 生成 → 推送。

设计见 `docs/proactive-multimodal.md` §5。**这一层只做编排**：
判断「该不该说」在 `gate.py`，判断「有没有由头」在 `triggers.py`，
生成文案在 `chat_graph`（复用），送出去在 `events/bus.py`。

## 为什么「没有订阅者就不生成」

主动消息通过 SSE 送给界面。**没人监听时，事件会被总线直接丢弃**
（见 `EventBus._deliver`）——那么生成就是纯粹浪费一次 LLM 调用，
还会往会话历史与记忆里写一条「用户从没见过的消息」。

所以订阅者检查放在**评估触发器之前**，并且**不消费意图**：
用户下次打开界面时，那个「早上该打个招呼」的意图仍然有效。
这也顺带解决了一个体验问题——关着桌宠过了一整天，晚上打开时
不会突然收到六条积压的主动消息。

## 为什么用轮询而不是精确调度

60 秒轮询 + 「到达时刻即触发」的判据，比给每个触发器算精确的下一次唤醒时刻
简单得多，且对**时钟跳变 / 休眠唤醒**天然健壮（笔记本合盖一夜再打开时，
精确调度会漏掉所有错过的时刻）。主动开口的时间精度要求本来就是「分钟级」。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from app.config import get_settings
from app.events.bus import Event, get_event_bus
from app.events.types import (
    EVENT_PERCEPTION_HINT,
    EVENT_PROACTIVE_MESSAGE,
    EVENT_PROACTIVE_SKIPPED,
    event_scope,
)
from app.perception.context import summarize
from app.perception.gather import (
    activity_fact as activity_fact_text,
    activity_summary,
    current_events,
    profile_facts as profile_fact_lines,
)
from app.perception.snapshot import get_perception_snapshot
from app.proactive.base import ProactiveTrigger
from app.proactive.gate import GateContext, evaluate_gate
from app.proactive.models import ProactiveIntent, TriggerContext
from app.proactive.prompt import is_declined
from app.proactive.state import get_state_store, local_now
from app.proactive.triggers import build_default_triggers

logger = logging.getLogger(__name__)


@dataclass
class ProactiveOutcome:
    """一次评估的结果（给 `/proactive/status` 与测试用）。"""

    fired: bool = False
    trigger_id: str = ""
    reason: str = ""
    skipped_reason: str = ""
    declined: bool = False
    reply: str = ""
    payload: dict = field(default_factory=dict)


class ProactiveRunner:
    """主动链路调度器（进程内单例）。"""

    def __init__(
        self,
        *,
        triggers: list[ProactiveTrigger] | None = None,
        settings_provider=get_settings,
    ) -> None:
        self._triggers = triggers
        self._settings_provider = settings_provider
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self.last_outcome: ProactiveOutcome | None = None

    # ---------- 触发器 ----------

    def triggers(self) -> list[ProactiveTrigger]:
        """当前触发器清单（配置可能变，因此每次现取）。"""
        if self._triggers is not None:
            return self._triggers
        return build_default_triggers(self._settings_provider())

    # ---------- 单轮评估 ----------

    async def run_once(self, *, now: datetime | None = None) -> ProactiveOutcome:
        """评估一轮。**任何异常都不向上抛**——它跑在后台任务里，
        抛出去只会静默吞掉整个调度循环（表现为「主动沟通突然不工作了」）。
        """
        settings = self._settings_provider()
        moment = now or local_now()
        outcome = ProactiveOutcome()

        if not settings.proactive_enabled:
            # 总开关关闭时**不发 skip 事件**：它每 60 秒评估一次，
            # 每次都广播就成了刷屏。用户自己关的，他知道为什么没消息。
            self.last_outcome = outcome
            return outcome

        persona_id = self._resolve_persona(settings)
        if not persona_id:
            self.last_outcome = outcome
            return outcome

        scope = event_scope(persona_id)
        bus = get_event_bus()
        if bus.subscriber_count(scope) == 0:
            # 没人听 → 不生成、**也不消费意图**（见模块文档）
            logger.debug("主动链路跳过：%s 无订阅者", scope)
            self.last_outcome = outcome
            return outcome

        store = get_state_store(settings.proactive_state_path)
        state = store.snapshot(now=moment)
        snapshot = get_perception_snapshot()
        desktop = snapshot.desktop(now=moment)
        # 行踪与画像由**这里**查好再放进上下文（触发器因此仍是纯函数，见 models.py）。
        # 每轮只查一次：三个消费者（两个新触发器 + 由头 facts）共用同一份结果。
        activity = activity_summary(now=moment, settings=settings)
        activity_fact = activity_fact_text(now=moment, settings=settings)
        profile_facts = profile_fact_lines(now=moment, settings=settings)

        intent = self._first_intent(
            TriggerContext(
                now=moment,
                persona_id=persona_id,
                last_user_message_at=state.last_user_message,
                last_proactive_at=state.last_proactive,
                fired=dict(state.fired),
                desktop=desktop,
                perception=tuple(snapshot.current(now=moment)),
                ongoing_facts=tuple(self._ongoing_facts(persona_id)),
                activity=activity,
                activity_fact=activity_fact,
                profile_facts=profile_facts,
            )
        )
        if intent is None:
            self.last_outcome = outcome
            return outcome

        decision = evaluate_gate(
            GateContext(
                now=moment,
                enabled=True,
                care=intent.care,
                quiet_hours=settings.proactive_quiet_hours,
                quiet_exempt_care=settings.proactive_quiet_exempt_care,
                min_interval_minutes=settings.proactive_min_interval_minutes,
                daily_quota=settings.proactive_daily_quota,
                conversation_window_minutes=settings.proactive_conversation_window_minutes,
                last_proactive_at=state.last_proactive,
                sent_today=state.sent_today,
                last_user_message_at=state.last_user_message,
                busy=self._is_busy(desktop),
                user_active=self._is_user_active(desktop),
            )
        )
        if not decision.allowed:
            outcome.skipped_reason = decision.reason
            outcome.trigger_id = intent.trigger_id
            bus.publish(
                scope,
                Event(
                    type=EVENT_PROACTIVE_SKIPPED,
                    payload={"trigger": intent.trigger_id, "reason": decision.reason},
                ),
            )
            logger.info("主动开口被拦下（%s）：%s", intent.trigger_id, decision.reason)
            self.last_outcome = outcome
            return outcome

        # 到这里才真正生成
        try:
            payload = await self._generate(intent, persona_id, settings)
        except Exception as exc:  # noqa: BLE001 - 生成失败只记日志，不影响后续调度
            logger.warning("主动开口生成失败（已忽略）：%s", exc)
            # 记去重键避免每分钟重试一次；但**不计入配额**（没有真的开口）
            store.note_declined(moment, fired_key=intent.dedupe_key)
            self.last_outcome = outcome
            return outcome

        reply = str(payload.get("reply") or "")
        outcome.trigger_id = intent.trigger_id
        outcome.reason = intent.reason
        outcome.reply = reply

        if is_declined(reply):
            # 角色决定不说：**不推送、不计数**，只记去重键（见 note_declined）
            outcome.declined = True
            store.note_declined(moment, fired_key=intent.dedupe_key)
            logger.info("主动开口：角色选择不说（%s）", intent.trigger_id)
            self.last_outcome = outcome
            return outcome

        outcome.fired = True
        outcome.payload = payload
        bus.publish(scope, Event(type=EVENT_PROACTIVE_MESSAGE, payload=payload))
        store.note_proactive(moment, fired_key=intent.dedupe_key)

        # 感知提示：让界面能显示「此刻在听什么」这类徽标（可观测性，不是必须）
        summary = summarize(current_events(now=moment, settings=settings))
        if summary:
            bus.publish(
                scope,
                Event(
                    type=EVENT_PERCEPTION_HINT,
                    payload={"source": "desktop", "kind": "summary", "summary": summary},
                ),
            )

        logger.info(
            "主动开口已推送（%s）：%s", intent.trigger_id, reply[:40]
        )
        self.last_outcome = outcome
        return outcome

    # ---------- 内部 ----------

    def _resolve_persona(self, settings) -> str:
        """主动开口面向哪个角色。

        优先 `PROACTIVE_PERSONA_ID`（部署指定）；为空则读**用户当前偏好**
        ——与启动期酒馆同步同一口径：非请求上下文只能读偏好。

        **必须校验角色还在**：用户可能在创作工坊里把内置角色删了
        （删除写用户侧隐藏清单，包内文件不改），而偏好里存的还是那个 id。
        不校验的话，每轮调度都会撞一次 404、打一条 warning——
        表现是「日志一直在报未知人设」，而真正该做的是静默跳过。
        """
        explicit = (settings.proactive_persona_id or "").strip()
        if explicit:
            return explicit
        try:
            from app.api.chat import current_persona_id, get_persona_presets  # noqa: PLC0415

            persona_id = current_persona_id()
            if persona_id and persona_id not in get_persona_presets():
                logger.info(
                    "主动链路跳过：当前偏好里的角色 %r 已不存在"
                    "（可能被隐藏或删除），请在界面上重新选一个陪伴对象",
                    persona_id,
                )
                return ""
            return persona_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("无法确定主动开口的角色，跳过：%s", exc)
            return ""

    def _first_intent(self, ctx: TriggerContext) -> ProactiveIntent | None:
        """按注册顺序取第一个命中的意图。

        刻意**不**做「多意图择优」：内置触发器的语义互不重叠
        （该问候 / 该关心 / 有由头），同时命中时先注册的赢就够了。
        引入打分排序会让「为什么这次是它说话」变得无法解释。
        """
        for trigger in self.triggers():
            try:
                intent = trigger.evaluate(ctx)
            except Exception as exc:  # noqa: BLE001 - 单个触发器坏了不该拖垮其余
                logger.warning("触发器 %s 评估异常（已跳过）：%s", trigger.id, exc)
                continue
            if intent is not None:
                return intent
        return None

    def _ongoing_facts(self, persona_id: str):
        """取该陪伴对象「进行中」且仍活跃的事实（供记忆到期触发器）。

        失败一律返回空列表：记忆后端没就绪 / 表还没建 / 库损坏时，
        该触发器静默失效，其余触发器照常工作——一个数据源不可用
        不该让整条主动链路哑掉（那正是「因为它不说话所以我不说话」）。
        """
        try:
            from app.api.chat import get_memory_store  # noqa: PLC0415 - 避免模块级循环

            store = get_memory_store()
            if store is None:
                return []
            return store.ongoing_facts(persona_id)
        except Exception as exc:  # noqa: BLE001 - 见上
            logger.debug("读取进行中事项失败（记忆到期触发器本轮跳过）：%s", exc)
            return []

    @staticmethod
    def _is_busy(desktop) -> bool:
        """是否处于「不该打扰」的状态（闸门⑥）。

        判据只用**进程名**，不用窗口标题——标题是内容（可能是一封邮件），
        进程名是类别（会议 / 游戏）。用标题做忙碌判定等于把隐私变成功能依赖。
        实现与进程表见 `app/perception/busy.py`（纯函数，可单测）。
        """
        from app.perception.busy import is_busy  # noqa: PLC0415 - 避免模块级耦合

        return is_busy(desktop)

    @staticmethod
    def _is_user_active(desktop) -> bool:
        """用户此刻是否清醒（免打扰豁免的前提）。

        没有桌面情景时**返回 True**：Web 端拿不到空闲数据，
        如果因此判定「用户不在」，深夜豁免就永远不会生效——
        而 Web 端恰恰也是需要它的场景。
        """
        if desktop is None:
            return True
        from app.proactive.triggers import LATE_NIGHT_ACTIVE_IDLE_SECONDS

        return desktop.idle_seconds < LATE_NIGHT_ACTIVE_IDLE_SECONDS

    async def _generate(self, intent: ProactiveIntent, persona_id: str, settings) -> dict:
        """生成文案：**复用对话链路**（见模块文档与 `docs/proactive-multimodal.md` §5.4）。

        延迟导入 `app.api.chat`：那个模块反过来依赖本包的状态存储
        （`note_user_message`），模块级导入会形成循环。
        """
        from app.api.chat import (  # noqa: PLC0415
            ChatRequest,
            execute_turn,
            get_session_repository,
        )
        from app.proactive.prompt import build_proactive_input  # noqa: PLC0415

        # 接到**最近一段会话**上，而不是每次新建：
        # 主动消息应该是「接着刚才聊的」，在界面上也自然出现在同一串对话里。
        session_id: str | None = None
        try:
            recent = get_session_repository().list_sessions(persona_id=persona_id, limit=1)
            if recent:
                session_id = recent[0].session_id
        except Exception as exc:  # noqa: BLE001 - 取不到就新建，不影响功能
            logger.debug("主动开口取最近会话失败（将新建）：%s", exc)

        response = await execute_turn(
            ChatRequest(
                text=build_proactive_input(intent),
                session_id=session_id,
                persona_id=persona_id,
                proactive=True,
                proactive_reason=intent.reason,
            )
        )
        return {
            "session_id": response.session_id,
            "persona_id": response.persona_id,
            "reply": response.reply,
            "emotion": response.emotion,
            "speak": response.speak,
            "trigger_id": intent.trigger_id,
            "reason": intent.reason,
            "proactive": True,
        }

    # ---------- 后台循环 ----------

    async def loop(self) -> None:
        """后台调度循环（lifespan 启动，关闭时取消）。"""
        logger.info("主动链路调度已启动")
        while not self._stopping.is_set():
            settings = self._settings_provider()
            interval = max(5.0, float(settings.proactive_interval_seconds))
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)
                return  # 收到停止信号
            except asyncio.TimeoutError:
                pass
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败不能终止循环
                logger.warning("主动链路单轮评估失败（已忽略）：%s", exc)

    def start(self) -> asyncio.Task:
        """启动后台循环（幂等：已在跑就返回既有任务）。"""
        if self._task is not None and not self._task.done():
            return self._task
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self.loop())
        return self._task

    async def stop(self) -> None:
        """停止后台循环。"""
        self._stopping.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        logger.info("主动链路调度已停止")

    # ---------- 观测 ----------

    def status(self) -> dict:
        """状态（`GET /proactive/status` 与 `/health` 用）。"""
        settings = self._settings_provider()
        store = get_state_store(settings.proactive_state_path)
        state = store.snapshot()
        outcome = self.last_outcome
        return {
            "enabled": settings.proactive_enabled,
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": settings.proactive_interval_seconds,
            "quiet_hours": settings.proactive_quiet_hours,
            "min_interval_minutes": settings.proactive_min_interval_minutes,
            "daily_quota": settings.proactive_daily_quota,
            "sent_today": state.sent_today,
            "last_proactive_at": state.last_proactive_at,
            "last_user_message_at": state.last_user_message_at,
            "triggers": [t.describe() for t in self.triggers()],
            "last_outcome": (
                {
                    "fired": outcome.fired,
                    "trigger_id": outcome.trigger_id,
                    "skipped_reason": outcome.skipped_reason,
                    "declined": outcome.declined,
                }
                if outcome is not None
                else None
            ),
        }


#: 进程内单例
_runner: ProactiveRunner | None = None


def get_proactive_runner() -> ProactiveRunner:
    """取调度器单例。"""
    global _runner
    if _runner is None:
        _runner = ProactiveRunner()
    return _runner


def set_proactive_runner(runner: ProactiveRunner | None) -> None:
    """替换/重置调度器（测试注入用）。"""
    global _runner
    _runner = runner
