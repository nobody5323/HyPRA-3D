"""节制层（Gate）：主动沟通能不能用，全看这一层。

## 为什么单独成一个纯函数模块

主动沟通的体验成败**九成在节制**：漏一次是打扰，漏十次是骚扰。
而这一层最容易在演进中被「顺手加个 if」搞坏——所以它：
- **纯函数**（输入全是值、无 IO、无全局状态）；
- 与调度器、与 LLM、与事件总线**完全解耦**；
- 可以逐条闸门单测（见 `tests/test_proactive/test_gate.py`）。

对齐项目里 `petGeometry.ts` / `avatar-source.ts` 的「纯函数 + 单测钉住」风格。

## 七道闸门（顺序即优先级，任一不过即 skip）

| # | 闸门 | 默认 | 说明 |
| --- | --- | --- | --- |
| 1 | 总开关 | 开 | 关则全停，一键且立即生效 |
| 2 | 免打扰时段 | 23:00–08:00 | **关怀型触发 + 用户清醒**可豁免 |
| 3 | 冷却 | 距上次主动 ≥ 90 分钟 | 两条主动消息不能挨着 |
| 4 | 每日配额 | ≤ 6 条 | |
| 5 | 用户正在对话中 | 距上次输入 < 2 分钟 | 此时该**回应**，不该另起话头 |
| 6 | 感知到忙碌 | 全屏 / 会议 / 游戏 | 由 `busy` 传入（宿主判定） |
| 7 | 同一触发去重 | 由触发器自报 | 在 `runner` 里按 `dedupe_key` 落地 |

> **「默认开」的完整含义**：上面 2~6 条同时默认生效。
> 不是因为「演示好看」就放开，而是「开着的默认值本身就是克制的」——
> 用户什么都不设，体验也已经是收敛的。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

#: 免打扰时段解析失败时的兜底（宁可多安静一会儿，也不要半夜开口）
DEFAULT_QUIET_HOURS = "23:00-08:00"


def parse_hhmm(value: str) -> time | None:
    """解析 `HH:MM`；失败返回 None（调用方决定兜底策略）。"""
    text = (value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def parse_quiet_hours(spec: str) -> tuple[time, time] | None:
    """解析 `HH:MM-HH:MM`；无法解析返回 None。

    支持跨零点（`23:00-08:00`）——这是**最常见**的免打扰形态，
    因此必须支持，而不是要求用户写成两段。
    """
    text = (spec or "").strip()
    if "-" not in text:
        return None
    left, _, right = text.partition("-")
    start, end = parse_hhmm(left), parse_hhmm(right)
    if start is None or end is None:
        return None
    return start, end


def in_time_window(moment: time, spec: str) -> bool:
    """`moment` 是否落在 `HH:MM-HH:MM` 窗口内（左闭右开）。

    支持跨零点（`23:00-08:00`）——这是**最常见**的免打扰形态，
    因此必须支持，而不是要求用户写成两段。

    区间语义是**左闭右开** `[start, end)`：`23:00-08:00` 表示 23:00 起安静，
    08:00 那一刻已经可以说话——否则「到点解除」会晚一分钟，
    而这一分钟正是用户刚打开电脑的时候。

    解析失败时回落默认窗口（宁可多安静一会儿，也不要半夜开口）。
    """
    window = parse_quiet_hours(spec) or parse_quiet_hours(DEFAULT_QUIET_HOURS)
    assert window is not None  # DEFAULT_QUIET_HOURS 必然可解析
    start, end = window
    if start == end:
        # 起止相同 = 全天命中（比「完全不命中」更符合直觉：写错了宁可少说话）
        return True
    if start < end:
        return start <= moment < end
    # 跨零点：`moment` 在 [start, 24:00) 或 [00:00, end)
    return moment >= start or moment < end


def in_quiet_hours(moment: time, spec: str) -> bool:
    """免打扰判定（`in_time_window` 的语义化别名，让调用点自解释）。"""
    return in_time_window(moment, spec)


@dataclass(frozen=True)
class GateContext:
    """闸门输入（全是值，便于单测逐条构造）。"""

    now: datetime
    enabled: bool = True
    #: 本次意图是否关怀型（可豁免免打扰）
    care: bool = False
    quiet_hours: str = DEFAULT_QUIET_HOURS
    quiet_exempt_care: bool = True
    min_interval_minutes: int = 90
    daily_quota: int = 6
    conversation_window_minutes: int = 2
    last_proactive_at: datetime | None = None
    sent_today: int = 0
    last_user_message_at: datetime | None = None
    #: 是否感知到用户忙碌（全屏 / 会议 / 游戏）。由宿主判定后传入——
    #: 判定规则依赖平台（Electron 的进程名表），不该塞进纯函数里。
    busy: bool = False
    #: 免打扰豁免是否需要「用户此刻清醒」这一条件。
    #: 深夜豁免的依据不是「现在几点了」，而是「用户还在活动」——
    #: 后者才说明他醒着，说话不会吵到人。
    user_active: bool = True


@dataclass(frozen=True)
class GateDecision:
    """闸门结论。`reason` 会随 `proactive_skipped` 事件广播给界面。"""

    allowed: bool
    reason: str = ""


def evaluate_gate(ctx: GateContext) -> GateDecision:
    """逐条过闸门，返回第一个拦住它的原因。"""
    # ① 总开关
    if not ctx.enabled:
        return GateDecision(False, "主动沟通已关闭")

    quiet = in_quiet_hours(ctx.now.time(), ctx.quiet_hours)
    # ② 免打扰时段
    if quiet:
        exempt = ctx.care and ctx.quiet_exempt_care and ctx.user_active
        if not exempt:
            return GateDecision(False, f"当前是免打扰时段（{ctx.quiet_hours}）")

    # ③ 冷却：两条主动消息的最小间隔
    if ctx.last_proactive_at is not None:
        elapsed = (ctx.now - ctx.last_proactive_at).total_seconds() / 60.0
        if elapsed < ctx.min_interval_minutes:
            remaining = int(ctx.min_interval_minutes - elapsed)
            return GateDecision(
                False, f"距上次主动开口不足 {ctx.min_interval_minutes} 分钟（还需 {remaining} 分钟）"
            )

    # ④ 每日配额
    if ctx.sent_today >= ctx.daily_quota:
        return GateDecision(False, f"今天已经主动开口 {ctx.sent_today} 次（上限 {ctx.daily_quota}）")

    # ⑤ 用户正在对话中：此时该**回应**，不该另起话头。
    #    这条常被忽略但很关键——用户刚发完消息，模型再主动搭一句，
    #    体验上是「抢话」。
    if ctx.last_user_message_at is not None:
        since_user = (ctx.now - ctx.last_user_message_at).total_seconds() / 60.0
        if since_user < ctx.conversation_window_minutes:
            return GateDecision(False, "用户刚刚说过话（此刻应该回应，而不是另起话头）")

    # ⑥ 感知到忙碌
    if ctx.busy:
        return GateDecision(False, "用户似乎正忙（全屏 / 会议 / 游戏）")

    return GateDecision(True)
