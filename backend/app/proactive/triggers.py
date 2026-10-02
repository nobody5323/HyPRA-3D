"""内置触发器：定时 / 间隔 / 情景三类（P1 与 P3 的范围）。

设计见 `docs/proactive-multimodal.md` §5.2。**每个触发器都只出「意图」**。

| 触发器 | 判据 | 关怀型 | 需要感知 |
| --- | --- | --- | --- |
| `daily-greeting` | 每天到了设定时刻 | 否 | 否 |
| `idle-checkin` | 用户静默超过 N 小时 | 否 | 否 |
| `memory-followup` | 进行中的事项搁置超过 N 天 | 否 | 否（要记忆） |
| `late-night` | 深夜且用户还在活动 | **是** | 桌面情景 |
| `now-playing` | 用户正在听一首新歌 | 否 | 桌面情景 |
| `low-battery` | 电量低且未充电 | **是** | 桌面情景 |
| `back-from-away` | 离开一阵子后刚回到电脑前 | 否 | **行踪** |
| `activity-shift` | 从「在做事」切到「在放松」 | 否 | **行踪** |

## 「关怀型」为什么是个独立标记

闸门②（免打扰）默认拦掉深夜。但**深夜恰恰是情感陪伴最需要它的时候**——
用户凌晨两点还醒着，正是最该有人说话的时刻。免打扰的目的是
「别吵醒睡着的人」，不是「看着你熬夜也不说话」。

所以关怀型触发带一个豁免，且豁免的**前提是「用户此刻清醒」**
（`GateContext.user_active`）——「现在几点」不是清醒的证据，
「刚刚还在敲键盘」才是。`late-night` 用空闲秒数做这个判据。

## 去重键的窗口由触发器自己定

- 「今天已问候过」→ 日粒度（`...:2026-09-29`）；
- 「这首歌已经聊过」→ 日粒度 + 歌名（同一首歌第二天可以再聊）；
- 「这次回来已经打过招呼」→ 用**当前段的开始时刻**（每次回来都是一个新时刻，
  因此每次都能说一句；真正的频率控制交给闸门③的冷却）；
- 不设去重键 → 每次评估都可能触发（内置触发器都不用这种）。

## 有由头才开口

`daily-greeting` / `idle-checkin` 这两个「没由头」的触发器，会把行踪与画像
（`ctx.activity_fact` / `ctx.profile_facts`）拼进 `reason` 与 `facts`。
理由见 §5.4：主动开口必须**有由头**，否则就是一句空泛的问候——
而「你最近一直在写代码，今天也顺利吗」与「早上好」是完全不同的两句话。
`reason` 同时是记忆召回的 query，带上具体信息也能召回到更相关的记忆。
"""

from __future__ import annotations

from datetime import time

from app.perception.timeline import FOCUS_CATEGORIES, LEISURE_CATEGORIES
from app.proactive.base import ProactiveTrigger
from app.proactive.gate import parse_hhmm, in_time_window
from app.proactive.models import ProactiveIntent, TriggerContext
from app.proactive.state import ensure_aware

#: 深夜窗口（跨零点）
LATE_NIGHT_WINDOW = "23:00-04:00"

#: 「用户还在活动」的空闲秒数上限：超过它说明人不在电脑前，
#: 那就不该按「深夜关怀」处理（人可能只是忘了关屏幕）。
LATE_NIGHT_ACTIVE_IDLE_SECONDS = 600.0

#: 低电量阈值（%）
LOW_BATTERY_PERCENT = 20.0

#: 「久别回来」的默认阈值（分钟）
BACK_FROM_AWAY_MINUTES = 30.0

#: 「忙完了」的默认阈值（分钟）
ACTIVITY_SHIFT_MINUTES = 20.0


def _date_of(iso: str) -> str:
    return (iso or "")[:10]


def _fired_on(ctx: TriggerContext, key: str, day: str) -> bool:
    """该去重键是否**在今天**已经触发过。"""
    return _date_of(ctx.fired.get(key, "")) == day


def _context_facts(ctx: TriggerContext, *, include_profile: bool = False) -> tuple[str, ...]:
    """把行踪（与可选的画像）拼成给模型的「由头」事实。

    刻意**只取画像的第一行**（最常用的那个应用）：画像的整体已经由
    感知层 `[此刻]` 注入了，这里再全量重复一遍只是白占预算——
    而 `facts` 会进内部消息，它要的是「一两句能开话头的具体信息」。
    """
    facts: list[str] = []
    if ctx.activity_fact:
        facts.append(ctx.activity_fact)
    if include_profile and ctx.profile_facts:
        facts.append(ctx.profile_facts[0])
    return tuple(facts)


class DailyTimeTrigger(ProactiveTrigger):
    """每天到了设定时刻开口一次（如早上 8:00 打个招呼）。

    会带上「他最近在忙什么」当由头：同样是早上九点，
    「早上好呀」与「早上好，昨天又写代码写到很晚吧」是两种关系体验。
    """

    def __init__(self, *, at: str = "09:00", trigger_id: str = "daily-greeting") -> None:
        self.id = trigger_id
        self.at = at
        self._at_time: time | None = parse_hhmm(at)

    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        if self._at_time is None:
            return None  # 配置写错 = 该触发器关闭（不猜一个时刻出来）
        if ctx.now.time() < self._at_time:
            return None
        day = ctx.now.strftime("%Y-%m-%d")
        key = f"{self.id}:{day}"
        if _fired_on(ctx, key, day):
            return None
        reason = f"到了每天 {self.at} 这个时刻，可以自然地打个招呼。"
        if ctx.activity_fact:
            reason += f"你注意到：{ctx.activity_fact}。"
        return ProactiveIntent(
            trigger_id=self.id,
            reason=reason,
            urgency=0.3,
            dedupe_key=key,
            facts=_context_facts(ctx, include_profile=True),
        )


class IdleIntervalTrigger(ProactiveTrigger):
    """用户静默超过 N 小时就问一句。"""

    def __init__(self, *, hours: float = 6.0, trigger_id: str = "idle-checkin") -> None:
        self.id = trigger_id
        self.hours = float(hours)

    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        if self.hours <= 0 or ctx.last_user_message_at is None:
            # 从未聊过就不主动开口：第一次接触应该是用户发起的
            # （对着一个还没打过招呼的人先说话，会让人不知所措）
            return None
        elapsed_hours = (ctx.now - ctx.last_user_message_at).total_seconds() / 3600.0
        if elapsed_hours < self.hours:
            return None
        day = ctx.now.strftime("%Y-%m-%d")
        key = f"{self.id}:{day}"
        if _fired_on(ctx, key, day):
            return None
        reason = f"用户已经大约 {elapsed_hours:.0f} 小时没有说话了。"
        if ctx.activity_fact:
            # 带上「他这段时间在干什么」——不然这句问候就是凭空来的
            reason += f"他最近在电脑上：{ctx.activity_fact}。"
        return ProactiveIntent(
            trigger_id=self.id,
            reason=reason,
            urgency=0.4,
            dedupe_key=key,
            facts=_context_facts(ctx),
        )


class LateNightTrigger(ProactiveTrigger):
    """深夜 + 用户还在活动 → 关怀型开口（**可豁免免打扰**）。"""

    def __init__(
        self,
        *,
        window: str = LATE_NIGHT_WINDOW,
        active_idle_seconds: float = LATE_NIGHT_ACTIVE_IDLE_SECONDS,
        trigger_id: str = "late-night",
    ) -> None:
        self.id = trigger_id
        self.window = window
        self.active_idle_seconds = active_idle_seconds

    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        desktop = ctx.desktop
        if desktop is None:
            return None  # Web 端没有桌面情景，该触发器静默失效
        if not in_time_window(ctx.now.time(), self.window):
            return None
        if desktop.idle_seconds >= self.active_idle_seconds:
            # 人不在（屏幕亮着但没输入）→ 不开口。这条把「深夜关怀」
            # 与「半夜吵醒人」分开了。
            return None
        day = ctx.now.strftime("%Y-%m-%d")
        key = f"{self.id}:{day}"
        if _fired_on(ctx, key, day):
            return None

        moment = (desktop.local_time or ctx.now.strftime("%H:%M")).strip()
        return ProactiveIntent(
            trigger_id=self.id,
            reason=f"现在是 {moment}，用户还在活动。",
            urgency=0.7,
            care=True,
            dedupe_key=key,
            facts=(f"现在是 {moment}",),
        )


class NowPlayingTrigger(ProactiveTrigger):
    """用户在听一首歌 → 以歌为「由头」聊两句。

    这是 P3 的旗舰演示（「听到你在听歌，主动聊两句」）。
    刻意**不做**任何「歌好不好听」的预判——那是人设的事；
    这里只提供「他在听什么」这个事实。
    """

    def __init__(self, *, trigger_id: str = "now-playing") -> None:
        self.id = trigger_id

    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        for event in reversed(ctx.perception):
            if event.kind != "now_playing":
                continue
            title = str(event.payload.get("title") or "").strip()
            if not title:
                continue
            day = ctx.now.strftime("%Y-%m-%d")
            key = f"{self.id}:{day}:{title}"
            if _fired_on(ctx, key, day):
                return None
            artist = str(event.payload.get("artist") or "").strip()
            display = f"《{title}》" + (f"—— {artist}" if artist else "")
            return ProactiveIntent(
                trigger_id=self.id,
                reason=f"用户正在听 {display}。",
                urgency=0.5,
                dedupe_key=key,
                facts=(f"用户正在听 {display}",),
            )
        return None


class LowBatteryTrigger(ProactiveTrigger):
    """电量低且未充电 → 提醒（关怀型）。"""

    def __init__(
        self, *, percent: float = LOW_BATTERY_PERCENT, trigger_id: str = "low-battery"
    ) -> None:
        self.id = trigger_id
        self.percent = percent

    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        desktop = ctx.desktop
        if desktop is None or desktop.battery_percent is None:
            return None
        if desktop.battery_charging is True or desktop.battery_percent > self.percent:
            return None
        day = ctx.now.strftime("%Y-%m-%d")
        key = f"{self.id}:{day}"
        if _fired_on(ctx, key, day):
            return None
        return ProactiveIntent(
            trigger_id=self.id,
            reason=f"设备电量只剩 {int(desktop.battery_percent)}%，而且没有在充电。",
            urgency=0.6,
            care=True,
            dedupe_key=key,
            facts=(f"设备电量 {int(desktop.battery_percent)}%，未充电",),
        )


class MemoryFollowUpTrigger(ProactiveTrigger):
    """进行中的事项搁置太久 → 回头问一句。

    这是「被记得」这件事最具体的形态：「你上次说在准备面试，后来怎么样了？」
    ——它靠的是**语义记忆里的进行中事项**（`FactType.ONGOING`），
    而不是对话里恰好又提到了什么。

    三条刻意的设计：

    1. **同一件事只问一次**（`dedupe_key` 用 fact_id 且不含日期）。
       问两遍是唠叨，而「没问出来结果」本身不构成再问的理由——
       用户想说的话，下一轮对话里自然会接上。
    2. **不豁免免打扰**（`care=False`）。关心一件私事在凌晨两点问出来是奇怪的，
       而「深夜还在活动」那种**当下**的情形才需要例外。
    3. **挑最久没被提起的那一件**（`ctx.ongoing_facts` 已按此升序）——
       一次只问一件，问多了像在做待办清单。
    """

    def __init__(self, *, days: float = 3.0, trigger_id: str = "memory-followup") -> None:
        self.id = trigger_id
        self.days = float(days)

    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        if self.days <= 0:
            return None  # 配置为 0 或负数 = 关闭该触发器
        for fact in ctx.ongoing_facts:
            key = f"{self.id}:{fact.fact_id}"
            if ctx.fired.get(key):
                continue
            reference = ensure_aware(fact.last_seen_at or fact.created_at)
            age_days = (ctx.now - reference).total_seconds() / 86400.0
            if age_days < self.days:
                continue
            summary = fact.summary_text or fact.detail
            if not summary:
                continue
            return ProactiveIntent(
                trigger_id=self.id,
                reason=(
                    f"用户有一件进行中的事已经 {age_days:.0f} 天没提起了：{summary}"
                ),
                urgency=0.5,
                care=False,
                dedupe_key=key,
                facts=(f"用户之前提过：{summary}",),
            )
        return None


class BackFromAwayTrigger(ProactiveTrigger):
    """用户离开一阵子后刚回到电脑前 → 打个招呼。

    为什么值得单独一个触发器：这是**唯一**一种「用户什么都没做、但状态变了」
    的时刻。别的触发器要么是「时间到了」（定时 / 间隔），要么是「有事发生」
    （听到歌、电量低）。只有它是「他回来了」——而那句「回来啦」
    正是陪伴感最直接的形态，也是「行踪」这项能力最自然的一次兑现。

    **判据来自行踪，不是 `idle_seconds`**：空闲秒数在用户回来之后立刻归零，
    拿它判「他刚才离开了多久」在结构上就做不到。行踪里
    「上一段结束」与「这一段开始」之间的空隙才是离开时长
    （见 `ActivitySummary.away_seconds`）。

    去重键用**当前段的开始时刻**：每次回来都是一个新时刻，因此每次都能说一句；
    真正的频率控制交给闸门③（冷却 30 分钟）与闸门④（每日配额）——
    这正是 §5.3 说的「真正防轰炸的是冷却与对话中不插话，不是去重」。
    """

    def __init__(
        self, *, minutes: float = BACK_FROM_AWAY_MINUTES, trigger_id: str = "back-from-away"
    ) -> None:
        self.id = trigger_id
        self.minutes = float(minutes)

    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        if self.minutes <= 0:
            return None  # 配置为 0 或负数 = 关闭该触发器
        summary = ctx.activity
        current = summary.current if summary is not None else None
        if summary is None or current is None:
            return None
        away_seconds = summary.away_seconds
        if away_seconds < self.minutes * 60.0:
            return None
        key = f"{self.id}:{current.started_at}"
        if ctx.fired.get(key):
            return None

        minutes = int(away_seconds // 60)
        if minutes >= 60:
            away_text = f"{minutes // 60} 小时 {minutes % 60} 分钟"
        else:
            away_text = f"{minutes} 分钟"
        facts = [f"用户离开了大约 {away_text}，刚回到电脑前"]
        if ctx.activity_fact:
            facts.append(ctx.activity_fact)
        return ProactiveIntent(
            trigger_id=self.id,
            reason=f"用户离开了大约 {away_text}，刚刚回到电脑前（现在在用 {current.process}）。",
            urgency=0.5,
            care=False,
            dedupe_key=key,
            facts=tuple(facts),
        )


class ActivityShiftTrigger(ProactiveTrigger):
    """从「在做事」切到「在放松」→ 问一句「忙完了？」

    只做**一个方向**：做事 → 放松。反方向（娱乐切回工作）刻意不触发——
    那时用户正要进入状态，开口就是打断。这个不对称是有意的：
    陪伴要出现在「可以被打断」的时刻，而不是「不该被打断」的时刻。

    `FOCUS_CATEGORIES` / `LEISURE_CATEGORIES` 由 `timeline.py` 定义，
    与行踪分类是同一份表——不在这里另判一次「什么算工作」。
    """

    def __init__(
        self, *, minutes: float = ACTIVITY_SHIFT_MINUTES, trigger_id: str = "activity-shift"
    ) -> None:
        self.id = trigger_id
        self.minutes = float(minutes)

    def evaluate(self, ctx: TriggerContext) -> ProactiveIntent | None:
        if self.minutes <= 0:
            return None  # 配置为 0 或负数 = 关闭该触发器
        summary = ctx.activity
        if summary is None:
            return None
        current = summary.current
        previous = summary.previous
        if current is None or previous is None:
            return None
        if current.category not in LEISURE_CATEGORIES:
            return None
        if previous.category not in FOCUS_CATEGORIES:
            return None
        if current.seconds(now=ctx.now) < self.minutes * 60.0:
            # 刚切过去一会儿：可能只是点开看一眼，还不能说「忙完了」
            return None
        key = f"{self.id}:{current.started_at}"
        if ctx.fired.get(key):
            return None
        return ProactiveIntent(
            trigger_id=self.id,
            reason=(
                f"用户刚从一个专注的状态（{previous.process}）切到了放松"
                f"（{current.process}），已经持续了一会儿。"
            ),
            urgency=0.45,
            care=False,
            dedupe_key=key,
            facts=(f"用户刚从 {previous.process} 切到了 {current.process}",),
        )


def build_default_triggers(settings) -> list[ProactiveTrigger]:
    """按配置组装默认触发器清单。

    **总是全部注册**，由触发器自己在配置无效时返回 None（如 `at=""` 解析失败）。
    这样「关掉某个触发器」不需要在组装处写一堆 if——加一种触发器时，
    只需在类里处理自己的「关闭」语义，不必回来改这里。

    顺序即优先级（`_first_intent` 取第一个命中的）：先关怀型（深夜 / 电量告急），
    再「他回来了 / 忙完了」这类由头型，最后是「听到歌」这种闲聊型。
    """
    return [
        DailyTimeTrigger(at=settings.proactive_daily_time),
        IdleIntervalTrigger(hours=settings.proactive_idle_hours),
        MemoryFollowUpTrigger(days=settings.proactive_memory_followup_days),
        LateNightTrigger(),
        BackFromAwayTrigger(minutes=settings.proactive_back_from_away_minutes),
        ActivityShiftTrigger(minutes=settings.proactive_activity_shift_minutes),
        NowPlayingTrigger(),
        LowBatteryTrigger(),
    ]
