"""调度器集成测试：订阅者检查 → 触发 → 闸门 → 复用对话链路 → 推送。

用真实链路跑（mock 模型 + 内存记忆 + 临时会话库，见 conftest），
因为这里要验的核心恰恰是「**主动消息走的是同一条 chat_graph**」——
用替身把链路换掉就验不到这件事了。
"""

import asyncio
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.events.bus import get_event_bus
from app.events.types import EVENT_PROACTIVE_MESSAGE, EVENT_PROACTIVE_SKIPPED, event_scope
from app.perception.models import DesktopContext
from app.perception.snapshot import get_perception_snapshot
from app.proactive.base import ProactiveTrigger
from app.proactive.models import ProactiveIntent
from app.proactive.prompt import DECLINE_MARKER
from app.proactive.runner import ProactiveRunner
from app.proactive.state import get_state_store

TZ = timezone(timedelta(hours=8))
PERSONA = "therapist-elder-sister"


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, tzinfo=TZ)


class _FixedTrigger(ProactiveTrigger):
    """固定命中的触发器（把「有没有由头」这件事从被测逻辑里摘出去）。"""

    id = "test-trigger"

    def __init__(self, *, care: bool = False, dedupe_key: str = "test:key") -> None:
        self.care = care
        self._dedupe_key = dedupe_key

    def evaluate(self, ctx) -> ProactiveIntent | None:
        return ProactiveIntent(
            trigger_id=self.id,
            reason="用户静默了很久。",
            care=self.care,
            dedupe_key=self._dedupe_key,
        )


class _BrokenTrigger(ProactiveTrigger):
    id = "broken"

    def evaluate(self, ctx):
        raise RuntimeError("故意炸掉")


def _settings(tmp_path, **overrides) -> Settings:
    values = dict(
        proactive_enabled=True,
        proactive_persona_id=PERSONA,
        proactive_state_path=str(tmp_path / "proactive-state.json"),
        # 关掉免打扰与冷却的干扰：这两个闸门在 test_gate.py 里单独验
        proactive_quiet_hours="00:00-00:01",
        proactive_min_interval_minutes=1,
        proactive_daily_quota=10,
        proactive_conversation_window_minutes=1,
    )
    values.update(overrides)
    return Settings(**values)


def _state(tmp_path, *, at: datetime):
    """读节流状态（**必须带上与 `run_once` 同一个 `now`**）。

    `snapshot()` 会按传入时刻滚动「今天说了几条」，不传就用**真实当天**。
    不传的话这些断言会退化成「只有当测试恰好在 2026-09-29 那天跑时才通过」——
    跨天即红，而红的原因（日期变了）与断言想验的东西（配额计数）毫无关系。
    """
    return get_state_store(str(tmp_path / "proactive-state.json")).snapshot(now=at)


def _runner(tmp_path, triggers, **overrides) -> ProactiveRunner:
    settings = _settings(tmp_path, **overrides)
    return ProactiveRunner(triggers=triggers, settings_provider=lambda: settings)


def test_disabled_switch_produces_nothing(tmp_path) -> None:
    """总开关关闭时**不发 skip 事件**——它每 60 秒评估一次，广播就成了刷屏。"""
    runner = _runner(tmp_path, [_FixedTrigger()], proactive_enabled=False)

    async def scenario():
        bus = get_event_bus()
        async with bus.subscribe(event_scope(PERSONA)) as queue:
            outcome = await runner.run_once(now=_at(14))
            assert outcome.fired is False
            assert queue.empty()

    asyncio.run(scenario())


def test_no_subscriber_skips_generation(tmp_path) -> None:
    """没人监听时不生成：事件会被总线丢弃，生成纯属浪费一次 LLM 调用。

    并且**不消费意图**——用户下次打开界面时那个意图仍然有效。
    """
    runner = _runner(tmp_path, [_FixedTrigger()])

    async def scenario():
        outcome = await runner.run_once(now=_at(14))
        assert outcome.fired is False
        # 关键：去重键没有被写入，下次仍然可以触发
        state = _state(tmp_path, at=_at(14))
        assert state.fired == {}

    asyncio.run(scenario())


def test_fires_and_publishes_when_subscribed(tmp_path) -> None:
    runner = _runner(tmp_path, [_FixedTrigger()])

    async def scenario():
        bus = get_event_bus()
        async with bus.subscribe(event_scope(PERSONA)) as queue:
            outcome = await runner.run_once(now=_at(14))
            assert outcome.fired is True
            assert outcome.reply.strip()

            event = queue.get_nowait()
            assert event.type == EVENT_PROACTIVE_MESSAGE
            # payload 与 POST /chat 的响应**同构**：前端复用同一套渲染
            assert event.payload["reply"] == outcome.reply
            assert "emotion" in event.payload
            assert "speak" in event.payload

            # 让后台记忆写入跑完，避免留下 pending task
            await asyncio.sleep(0.05)

    asyncio.run(scenario())

    state = _state(tmp_path, at=_at(14))
    assert state.sent_today == 1
    assert state.last_proactive is not None
    assert state.fired


def test_gate_block_publishes_skipped_and_does_not_generate(tmp_path) -> None:
    """被闸门拦下时要有可观测性——调参靠它，而不是靠猜「为什么它不说话」。"""
    runner = _runner(tmp_path, [_FixedTrigger()], proactive_daily_quota=0)

    async def scenario():
        bus = get_event_bus()
        async with bus.subscribe(event_scope(PERSONA)) as queue:
            outcome = await runner.run_once(now=_at(14))
            assert outcome.fired is False
            assert "上限" in outcome.skipped_reason

            event = queue.get_nowait()
            assert event.type == EVENT_PROACTIVE_SKIPPED
            assert event.payload["trigger"] == "test-trigger"
            assert event.payload["reason"] == outcome.skipped_reason

    asyncio.run(scenario())


def test_declined_reply_is_not_published(tmp_path) -> None:
    """角色选择不说：不推送、不计入配额，但**记去重键**——
    否则下一轮调度会再调一次模型，形成每分钟烧一次调用的死循环。
    """
    runner = _runner(tmp_path, [_FixedTrigger()])

    async def scenario():
        # 让模型回一个「不说」标记
        from app.api import chat as chat_module
        from app.llm.base import LLMProvider

        class _DecliningLLM(LLMProvider):
            name = "declining"
            model = "declining-model"

            def chat(self, messages, **kwargs):
                return DECLINE_MARKER

        chat_module.set_llm_provider(_DecliningLLM())

        bus = get_event_bus()
        async with bus.subscribe(event_scope(PERSONA)) as queue:
            outcome = await runner.run_once(now=_at(14))
            assert outcome.declined is True
            assert outcome.fired is False
            assert queue.empty()
            await asyncio.sleep(0.05)

    asyncio.run(scenario())

    state = _state(tmp_path, at=_at(14))
    assert state.sent_today == 0
    assert state.fired  # 去重键仍然记了


def test_proactive_turn_does_not_write_user_message(tmp_path) -> None:
    """主动轮的合成输入**不进会话历史**——它是内部指令，不是用户说的话。"""
    runner = _runner(tmp_path, [_FixedTrigger()])

    async def scenario():
        bus = get_event_bus()
        async with bus.subscribe(event_scope(PERSONA)) as queue:
            outcome = await runner.run_once(now=_at(14))
            assert outcome.fired
            await asyncio.sleep(0.05)

            from app.api.chat import get_session_repository

            session_id = outcome.payload["session_id"]
            turns = get_session_repository().list_history(session_id, limit=50)
            assert [turn.role for turn in turns] == ["assistant"]
            assert "[系统触发" not in turns[0].text

    asyncio.run(scenario())


def test_broken_trigger_does_not_break_others(tmp_path) -> None:
    """单个触发器坏了不该拖垮其余（否则加一种由头就能让整条链路哑掉）。"""
    runner = _runner(tmp_path, [_BrokenTrigger(), _FixedTrigger()])

    async def scenario():
        bus = get_event_bus()
        async with bus.subscribe(event_scope(PERSONA)) as queue:
            outcome = await runner.run_once(now=_at(14))
            assert outcome.fired is True
            assert outcome.trigger_id == "test-trigger"

    asyncio.run(scenario())


def test_no_intent_produces_nothing(tmp_path) -> None:
    class _NeverFires(ProactiveTrigger):
        id = "never"

        def evaluate(self, ctx):
            return None

    runner = _runner(tmp_path, [_NeverFires()])

    async def scenario():
        bus = get_event_bus()
        async with bus.subscribe(event_scope(PERSONA)) as queue:
            outcome = await runner.run_once(now=_at(14))
            assert outcome.fired is False
            assert outcome.skipped_reason == ""
            assert queue.empty()

    asyncio.run(scenario())


def test_care_trigger_exempts_quiet_hours_with_desktop_context(tmp_path) -> None:
    """深夜关怀链路：感知到用户清醒 → 越过免打扰开口（§5.3 的例外白名单）。"""
    runner = _runner(
        tmp_path,
        [_FixedTrigger(care=True)],
        proactive_quiet_hours="23:00-08:00",
        proactive_quiet_exempt_care=True,
    )
    get_perception_snapshot().record_desktop(
        DesktopContext(local_time="02:14", idle_seconds=5.0)
    )

    async def scenario():
        bus = get_event_bus()
        async with bus.subscribe(event_scope(PERSONA)) as queue:
            outcome = await runner.run_once(now=_at(2, 14))
            assert outcome.fired is True
            await asyncio.sleep(0.05)

    asyncio.run(scenario())


def test_status_exposes_throttle_state(tmp_path) -> None:
    runner = _runner(tmp_path, [_FixedTrigger()])
    status = runner.status()
    assert status["enabled"] is True
    assert status["running"] is False
    assert status["sent_today"] == 0
    assert [t["id"] for t in status["triggers"]] == ["test-trigger"]


def test_missing_persona_is_skipped_silently(tmp_path) -> None:
    """偏好里的角色已被隐藏/删除时**静默跳过**，而不是每轮撞一次 404。

    真实场景：用户在创作工坊里把内置角色删了（删除走用户侧隐藏清单，
    包内文件一字未改），而偏好里存的还是那个 id。不校验的话，
    调度日志会一直刷「未知人设」，而真正该做的是跳过这一轮。
    """
    from app.api import chat as chat_module

    # `proactive_persona_id=""` 才走「读用户偏好」那条路
    settings = _settings(tmp_path, proactive_persona_id="")
    runner = ProactiveRunner(
        triggers=[_FixedTrigger()],
        settings_provider=lambda: settings,
    )
    original = chat_module.current_persona_id
    try:
        chat_module.current_persona_id = lambda: "已被删除的角色"  # type: ignore[assignment]
        assert runner._resolve_persona(settings) == ""
    finally:
        chat_module.current_persona_id = original  # type: ignore[assignment]


def test_existing_persona_is_used(tmp_path) -> None:
    """正常情况：偏好里的角色还在，就照常使用。"""
    from app.api import chat as chat_module

    settings = _settings(tmp_path, proactive_persona_id="")
    runner = ProactiveRunner(triggers=[_FixedTrigger()], settings_provider=lambda: settings)
    original = chat_module.current_persona_id
    try:
        chat_module.current_persona_id = lambda: PERSONA  # type: ignore[assignment]
        assert runner._resolve_persona(settings) == PERSONA
    finally:
        chat_module.current_persona_id = original  # type: ignore[assignment]


def test_explicit_persona_bypasses_the_check(tmp_path) -> None:
    """显式配置的 `PROACTIVE_PERSONA_ID` 不参与存在性校验——部署方自己负责。"""
    runner = ProactiveRunner(triggers=[_FixedTrigger()], settings_provider=lambda: _settings(tmp_path))
    settings = _settings(tmp_path, proactive_persona_id="部署方指定")
    assert runner._resolve_persona(settings) == "部署方指定"


# --------------------------------------------------------------------------
# 行踪 / 画像 → 触发上下文（§4.9）
# --------------------------------------------------------------------------


class _RecordingTrigger(ProactiveTrigger):
    """记录自己拿到的上下文（不命中任何意图）。"""

    id = "recorder"

    def __init__(self) -> None:
        self.contexts: list = []

    def evaluate(self, ctx):
        self.contexts.append(ctx)
        return None


def test_runner_fills_activity_and_profile_into_context(tmp_path) -> None:
    """行踪与画像由 **runner** 查好再放进上下文——触发器因此仍是纯函数。

    断的是那条供给线：两个行踪驱动的触发器（`back-from-away` /
    `activity-shift`）与「有由头才开口」的 facts 全靠它。
    把查询塞进触发器里也能跑，但那样触发器就要持有轨迹单例、做 IO，
    也就没法逐条单测了。
    """
    from app.perception.profile import reset_profile_cache
    from app.perception.timeline import get_timeline

    timeline = get_timeline()
    timeline.observe(
        DesktopContext(foreground_process="Code.exe", idle_seconds=1.0), now=_at(19, 0)
    )
    timeline.observe(
        DesktopContext(foreground_process="Code.exe", idle_seconds=1.0), now=_at(21, 0)
    )
    reset_profile_cache()

    recorder = _RecordingTrigger()
    runner = _runner(tmp_path, [recorder])

    async def scenario():
        # 必须有订阅者：没人监听时 runner 在评估触发器**之前**就返回了
        # （见 runner 模块文档「为什么没有订阅者就不生成」）
        async with get_event_bus().subscribe(event_scope(PERSONA)):
            await runner.run_once(now=_at(21, 30))

    asyncio.run(scenario())

    assert recorder.contexts, "触发器没被评估到"
    ctx = recorder.contexts[0]
    assert ctx.activity is not None
    assert ctx.activity.latest is not None
    assert ctx.activity.latest.process == "Code.exe"
    assert "Code.exe" in ctx.activity_fact
    # 两小时的样本超过 30 分钟下限 → 画像成立
    assert ctx.profile_facts
    assert any("Code.exe" in line for line in ctx.profile_facts)


def test_runner_leaves_activity_empty_when_disabled(tmp_path) -> None:
    """行踪关掉时三个字段都为空——触发器静默失效，其余触发器照常工作。"""
    from app.perception.profile import reset_profile_cache
    from app.perception.timeline import get_timeline

    get_timeline().observe(
        DesktopContext(foreground_process="Code.exe", idle_seconds=1.0), now=_at(20, 0)
    )
    reset_profile_cache()

    recorder = _RecordingTrigger()
    runner = _runner(tmp_path, [recorder], perception_activity_enabled=False)

    async def scenario():
        async with get_event_bus().subscribe(event_scope(PERSONA)):
            await runner.run_once(now=_at(20, 30))

    asyncio.run(scenario())

    ctx = recorder.contexts[0]
    assert ctx.activity is None
    assert ctx.activity_fact == ""
    assert ctx.profile_facts == ()


def test_runner_survives_a_broken_timeline(tmp_path, monkeypatch) -> None:
    """行踪读不出来时**不该让整条主动链路哑掉**——那正是「因为它不说话所以我不说话」。"""
    import app.perception.timeline as timeline_module

    class _Exploding:
        def summary(self, **kwargs):
            raise RuntimeError("行踪炸了")

        def segments(self, **kwargs):
            raise RuntimeError("行踪炸了")

    monkeypatch.setattr(
        timeline_module, "get_timeline", lambda **kwargs: _Exploding()
    )

    recorder = _RecordingTrigger()
    runner = _runner(tmp_path, [recorder])

    async def scenario():
        async with get_event_bus().subscribe(event_scope(PERSONA)):
            await runner.run_once(now=_at(14))

    asyncio.run(scenario())

    ctx = recorder.contexts[0]
    assert ctx.activity is None
    assert ctx.activity_fact == ""
