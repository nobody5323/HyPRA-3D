"""内置工具集：情感陪伴场景的「能办事」能力（Agent 行动层）。

四个工具围绕**情绪调节干预**展开（而非泛用型工具），
共同支撑「从陪聊升级为可测量、可干预的陪伴」这一差异化定位：

| 工具 | 能力 | 价值 |
|---|---|---|
| `record_mood_journal` | 记录情绪日记 | 让情绪**可测量** |
| `query_mood_trend`    | 查询情绪趋势 | 让状态**可回顾** |
| `start_breathing_exercise` | 呼吸引导（含 SSML 节奏） | 让干预**可跟随**（数字人示范） |
| `recall_memory`       | 主动检索记忆 | 让记忆**可主动调用**（而非被动注入） |

依赖经由 ToolContext.extras 注入（见 registry.py），便于测试与替换。
"""

from pydantic import BaseModel, Field

from app.digital_human.ssml import build_speak_command
from app.memory.cold.mood_log import MoodLogEntry
from app.skills.registry import get_skill_registry
from app.tools.emotion import EMOTION_LABELS_ZH, EmotionLabel
from app.tools.registry import ToolContext, ToolRegistry, ToolSpec
from app.tools.skills import CTX_SKILL_REGISTRY, build_study_skill_tool

# extras 中的依赖键名（约定）
CTX_MOOD_STORE = "mood_store"
CTX_MEMORY_STORE = "memory_store"

# 中文标签 → 英文枚举（用于把模型给的中文情绪归一化）
_ZH_TO_ENUM: dict[str, str] = {}
for _key, _zh in EMOTION_LABELS_ZH.items():
    _ZH_TO_ENUM.setdefault(_zh, _key)


def normalize_emotion(value: str) -> str:
    """把情绪标签归一化为英文枚举值（兼容模型返回中文）。"""
    text = (value or "").strip()
    if not text:
        return EmotionLabel.NEUTRAL.value
    lowered = text.lower()
    if lowered in {label.value for label in EmotionLabel}:
        return lowered
    return _ZH_TO_ENUM.get(text, EmotionLabel.NEUTRAL.value)


# =============================================================
# 工具 1：记录情绪日记
# =============================================================


class RecordMoodArgs(BaseModel):
    emotion: str = Field(description="情绪标签，中文或英文（如「焦虑」或 anxious）")
    intensity: float = Field(default=0.5, ge=0.0, le=1.0, description="情绪强度 0-1")
    trigger: str = Field(default="", description="触发事件（如「工作汇报」「和家人通话」）")
    note: str = Field(default="", description="补充备注（可选）")


def _record_mood(args: RecordMoodArgs, ctx: ToolContext) -> dict:
    store = ctx.extras.get(CTX_MOOD_STORE)
    if store is None:
        return {"message": "情绪日记功能当前不可用，可以先跟我说说你的感受。"}

    emotion = normalize_emotion(args.emotion)
    entry_id = store.add(
        MoodLogEntry(
            companion_id=ctx.companion_id,
            emotion=emotion,
            intensity=args.intensity,
            trigger=args.trigger,
            note=args.note,
            session_id=ctx.session_id,
        )
    )
    label_zh = EMOTION_LABELS_ZH.get(emotion, emotion)
    detail = f"（{label_zh}，强度 {args.intensity:.1f}）"
    if args.trigger:
        detail += f"，触发：{args.trigger}"
    return {
        "message": f"已帮你记下这次感受{detail}。以后想回顾，随时问我。",
        "entry_id": entry_id,
        "emotion": emotion,
        "intensity": args.intensity,
    }


# =============================================================
# 工具 2：查询情绪趋势
# =============================================================


class QueryMoodTrendArgs(BaseModel):
    days: int = Field(default=7, ge=1, le=90, description="回看天数（默认 7 天）")


def _query_trend(args: QueryMoodTrendArgs, ctx: ToolContext) -> dict:
    store = ctx.extras.get(CTX_MOOD_STORE)
    if store is None:
        return {"message": "情绪记录功能当前不可用。"}

    trend = store.trend(ctx.companion_id, days=args.days)
    if trend["total"] == 0:
        return {
            "message": f"最近 {args.days} 天还没有记录过心情。想聊聊现在的感受吗？",
            "trend": trend,
        }

    distribution = trend["distribution"]
    parts = "、".join(
        f"{EMOTION_LABELS_ZH.get(k, k)} {v} 次"
        for k, v in sorted(distribution.items(), key=lambda kv: kv[1], reverse=True)
    )
    dominant_zh = EMOTION_LABELS_ZH.get(trend["dominant"], trend["dominant"])
    message = (
        f"最近 {args.days} 天你一共记录了 {trend['total']} 次心情：{parts}。"
        f"出现最多的是「{dominant_zh}」，平均强度 {trend['avg_intensity']}。"
    )
    return {"message": message, "trend": trend}


# =============================================================
# 工具 3：呼吸引导（含 SSML 节奏，供数字人示范）
# =============================================================

# 呼吸法配方：名称 / 说明 / 阶段（名称, 秒数）
BREATHING_PATTERNS: dict[str, dict] = {
    "478": {
        "name": "4-7-8 呼吸法",
        "description": "适合睡前放松、缓解失眠",
        "phases": [("吸气", 4), ("屏息", 7), ("呼气", 8)],
    },
    "box": {
        "name": "方块呼吸",
        "description": "适合焦虑、紧张时平复情绪",
        "phases": [("吸气", 4), ("屏息", 4), ("呼气", 4), ("屏息", 4)],
    },
}


class BreathingArgs(BaseModel):
    pattern: str = Field(
        default="478",
        description="呼吸法：478（助眠）| box（方块呼吸，平复焦虑）",
    )
    cycles: int = Field(default=4, ge=1, le=12, description="循环次数")


def _breathing(args: BreathingArgs, ctx: ToolContext) -> dict:
    pattern = BREATHING_PATTERNS.get((args.pattern or "478").lower())
    if pattern is None:
        pattern = BREATHING_PATTERNS["478"]

    phases = pattern["phases"]
    cycle_seconds = sum(seconds for _, seconds in phases)
    total_seconds = cycle_seconds * args.cycles

    guide = "跟着我的节奏："
    guide += "，".join(f"{name} {seconds} 秒" for name, seconds in phases)
    guide += f"，循环 {args.cycles} 次，大约 {total_seconds} 秒。"

    # 生成 SSML 引导（数字人可分段示范节奏）
    command = build_speak_command(
        f"我们慢慢来。{guide}",
        emotion=EmotionLabel.CALM.value,
        intensity=0.6,
    )

    return {
        "message": f"好，我们一起做 {pattern['name']}（{pattern['description']}）。{guide}",
        "pattern": args.pattern,
        "pattern_name": pattern["name"],
        "phases": [{"name": n, "seconds": s} for n, s in phases],
        "cycles": args.cycles,
        "cycle_seconds": cycle_seconds,
        "total_seconds": total_seconds,
        "ssml": command.ssml,
        "display_text": command.display_text,
    }


# =============================================================
# 工具 4：主动检索记忆
# =============================================================


class RecallMemoryArgs(BaseModel):
    query: str = Field(description="检索关键词或问题（如「妈妈」「工作压力」）")
    limit: int = Field(default=3, ge=1, le=10, description="返回条数上限")


def _recall_memory(args: RecallMemoryArgs, ctx: ToolContext) -> dict:
    store = ctx.extras.get(CTX_MEMORY_STORE)
    if store is None:
        return {"message": "记忆检索当前不可用。"}

    context = store.recall(ctx.companion_id, args.query)
    memories = [r.record.text for r in context.memories[: args.limit]]
    facts = [f.summary_text for f in context.facts[: args.limit]]

    if not memories and not facts:
        return {
            "message": f"关于「{args.query}」，我这边暂时没有相关记忆。",
            "memories": [],
            "facts": [],
        }

    pieces: list[str] = []
    if memories:
        pieces.append("相关回忆：" + "；".join(memories))
    if facts:
        pieces.append("已知事实：" + "；".join(facts))
    return {
        "message": "；".join(pieces),
        "memories": memories,
        "facts": facts,
    }


# =============================================================
# 工厂
# =============================================================


def build_default_registry(*, skill_registry=None) -> ToolRegistry:
    """构建默认工具注册表（四个情感陪伴工具 + 有技能时的 `study_skill`）。

    `study_skill` **只在存在启用技能时**注册：技能库为空还注册它，只会让模型多一个
    永远失败的选项，白占 tools schema 的 token。
    """
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="record_mood_journal",
            description=(
                "当用户表达了明显情绪（焦虑/难过/开心等）时，记录这一次情绪日记，"
                "便于后续回顾情绪趋势。记录后请自然地继续陪伴，不要生硬地宣告「已记录」。"
            ),
            parameters=RecordMoodArgs.model_json_schema(),
            args_model=RecordMoodArgs,
            handler=_record_mood,
            tags=["mood", "memory"],
        )
    )
    registry.register(
        ToolSpec(
            name="query_mood_trend",
            description=(
                "查询用户近期的情绪趋势（分布与平均强度）。"
                "当用户问「我最近状态怎么样」「我是不是一直很焦虑」时使用。"
            ),
            parameters=QueryMoodTrendArgs.model_json_schema(),
            args_model=QueryMoodTrendArgs,
            handler=_query_trend,
            tags=["mood"],
        )
    )
    registry.register(
        ToolSpec(
            name="start_breathing_exercise",
            description=(
                "引导用户做一次呼吸练习。当用户焦虑、紧张、失眠、情绪激动时使用；"
                "返回的 ssml 字段可交给数字人分段示范节奏。"
            ),
            parameters=BreathingArgs.model_json_schema(),
            args_model=BreathingArgs,
            handler=_breathing,
            tags=["intervention", "wellbeing"],
        )
    )
    registry.register(
        ToolSpec(
            name="recall_memory",
            description=(
                "主动检索与某个关键词相关的长期记忆。"
                "当你需要确认「用户以前是否提过这件事」时使用。"
            ),
            parameters=RecallMemoryArgs.model_json_schema(),
            args_model=RecallMemoryArgs,
            handler=_recall_memory,
            tags=["memory"],
        )
    )

    # 技能库非空才挂 study_skill（空库时它是纯粹的 schema 噪音）
    active_skills = skill_registry if skill_registry is not None else get_skill_registry()
    if len(active_skills.enabled()) > 0:
        registry.register(build_study_skill_tool())

    return registry


def build_tool_context(
    companion_id: str,
    *,
    session_id: str = "",
    user_name: str = "用户",
    mood_store=None,
    memory_store=None,
    skill_registry=None,
) -> ToolContext:
    """构造工具上下文（注入依赖）。

    `skill_registry` 缺省取全局技能库：它在启动时一次性装好、之后只读，
    没必要逐层往下传（需要隔离的测试仍可显式传入）。
    """
    extras: dict = {}
    if mood_store is not None:
        extras[CTX_MOOD_STORE] = mood_store
    if memory_store is not None:
        extras[CTX_MEMORY_STORE] = memory_store
    extras[CTX_SKILL_REGISTRY] = (
        skill_registry if skill_registry is not None else get_skill_registry()
    )
    return ToolContext(
        companion_id=companion_id,
        session_id=session_id,
        user_name=user_name,
        extras=extras,
    )
