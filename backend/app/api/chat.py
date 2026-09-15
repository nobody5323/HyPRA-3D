"""chat 路由（M3：LangGraph 编排）。

POST /chat 的完整链路由 LangGraph 节点图驱动：

    load_persona → worldbook_recall → memory_recall
        → assemble_prompt → generate_reply → write_memory

会话读写在路由层完成（图为无状态编排）：
    ① 取/建会话（携带 history 与状态变量）
    ② graph.invoke 生成回复并写入记忆
    ③ 历史追加 user/assistant 两轮
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.digital_human.ssml import build_speak_command
from app.graph.chat_graph import build_chat_graph
from app.graph.nodes import ChatNodes
from app.llm.base import LLMProvider
from app.llm.factory import create_llm_provider
from app.llm.profiles import list_presets, load_model_profiles, resolve_profile
from app.memory.cold.extractor import create_extractor
from app.memory.cold.mood_log import SqliteMoodLogStore
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.warm.factory import create_warm_store
from app.prompts.persona.loader import load_builtin_presets
from app.prompts.renderer import estimate_tokens
from app.prompts.style.loader import load_builtin_styles
from app.rag.prompt_manager import PromptManager
from app.session.context import ChatTurn
from app.session.repository import SessionRepository
from app.tools.builtin_tools import build_default_registry
from app.worldbook.loader import load_builtin_entries

router = APIRouter(prefix="/chat", tags=["chat"])

# 进程内单例（内存存储；后续里程碑替换为持久化/独立热层）
_repository = SessionRepository()
_presets = load_builtin_presets()
_entries = load_builtin_entries()
_styles = load_builtin_styles()
_memory_store: MemoryStore | None = None
_mood_store = None
_llm_provider: LLMProvider | None = None
_chat_graph = None
_model_profiles = None

_DEFAULT_PERSONA_ID = "therapist-elder-sister"


# ---------- 依赖懒加载（测试可经 set_* 注入）----------


def get_memory_store() -> MemoryStore:
    """懒加载记忆门面（冷层 SQLite + 温层 memory/qdrant + 规则抽取器）。"""
    global _memory_store
    if _memory_store is None:
        settings = get_settings()
        cold = SqliteColdStore(db_path=settings.cold_db_path)
        warm = create_warm_store(
            settings.warm_backend,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
        _memory_store = MemoryStore(
            cold,
            warm,
            fact_limit=settings.memory_fact_limit,
            memory_top_k=settings.memory_top_k,
            block_budget=settings.memory_block_budget,
            extractor=create_extractor(settings.memory_extractor),
        )
    return _memory_store


def set_memory_store(store: MemoryStore | None) -> None:
    """替换/重置记忆门面（同时失效已编译的图）。"""
    global _memory_store, _chat_graph
    _memory_store = store
    _chat_graph = None

def get_mood_store():
    """懒加载情绪日记库（Agent 工具用）。"""
    global _mood_store
    if _mood_store is None:
        settings = get_settings()
        _mood_store = SqliteMoodLogStore(db_path=settings.cold_db_path)
    return _mood_store


def set_mood_store(store) -> None:
    """替换/重置情绪日记库（测试注入用）。"""
    global _mood_store, _chat_graph
    _mood_store = store
    _chat_graph = None


def get_model_profiles():
    """懒加载模型预设档（进程内缓存，避免每轮对话都读盘）。"""
    global _model_profiles
    if _model_profiles is None:
        _model_profiles = load_model_profiles()
    return _model_profiles


def get_llm_provider() -> LLMProvider:
    """懒加载 LLM provider（默认 mock：无 key 可跑通对话链路）。"""
    global _llm_provider
    if _llm_provider is None:
        settings = get_settings()
        _llm_provider = create_llm_provider(
            settings.llm_provider,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout,
            enable_thinking=settings.llm_enable_thinking,
        )
    return _llm_provider


def set_llm_provider(provider: LLMProvider | None) -> None:
    """替换/重置 LLM provider（同时失效已编译的图）。"""
    global _llm_provider, _chat_graph
    _llm_provider = provider
    _chat_graph = None


def get_chat_graph():
    """懒加载并编译对话编排图（依赖变更时重建）。"""
    global _chat_graph
    if _chat_graph is None:
        settings = get_settings()
        provider = get_llm_provider()
        nodes = ChatNodes(
            presets=_presets,
            entries=_entries,
            memory_store=get_memory_store(),
            llm_provider=provider,
            prompt_manager=PromptManager(
                worldbook_budget=settings.worldbook_budget,
                memory_budget=settings.memory_block_budget,
                history_budget=settings.prompt_history_budget,
                total_budget=settings.prompt_total_budget,
                style_budget=settings.prompt_style_budget,
            ),
            styles=_styles,
            default_style_id=settings.style_preset,
            model_name=settings.llm_model,
            profiles=get_model_profiles(),
            tool_registry=build_default_registry() if settings.agent_tools_enabled else None,
            mood_store=get_mood_store() if settings.agent_tools_enabled else None,
            max_tool_rounds=settings.max_tool_rounds,
        )
        _chat_graph = build_chat_graph(nodes)
    return _chat_graph


# ---------- 请求 / 响应模型 ----------


class ChatRequest(BaseModel):
    """一次对话请求。"""

    text: str = Field(min_length=1, description="用户本次输入")
    session_id: str | None = Field(default=None, description="续聊时传入既有会话 id")
    persona_id: str = Field(default=_DEFAULT_PERSONA_ID, description="人设预设 id")
    user_name: str = Field(default="朋友", description="用户称呼")
    current_mood: str | None = Field(default=None, description="当前情绪标签（可选）")
    style_id: str | None = Field(
        default=None,
        description="文风预设 id（可选，缺省用服务端默认；用于 A/B 对比演示）",
    )
    preset_id: str | None = Field(
        default=None,
        description="模型预设 id（可选，缺省按当前模型名自动匹配；见 GET /chat/presets）",
    )


class ChatResponse(BaseModel):
    """一轮对话的完整结果。"""

    session_id: str
    persona_id: str
    reply: str = Field(description="assistant 回复（mock provider 为占位文本）")
    emotion: dict[str, object] = Field(
        default_factory=dict,
        description="情绪判定：label/label_zh/intensity/confidence/evidence/facial_expression/source",
    )
    system_prompt: str
    messages: list[dict[str, str]]
    worldbook_hits: list[str] = Field(description="命中的世界书条目 id")
    skipped: list[str] = Field(description="因预算被跳过的条目 id")
    memory_counts: dict[str, int] = Field(
        default_factory=dict, description="本轮召回的记忆数：memories/facts/summary"
    )
    remembered: dict[str, int] = Field(
        default_factory=dict, description="本轮写入的记忆数：facts/memory/summary"
    )
    warnings: list[str]
    estimated_tokens: int
    style: dict[str, object] = Field(
        default_factory=dict,
        description="本轮文风与采样：style_id/style_name/examples/sampling",
    )
    preset: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "本轮模型预设（实际生效）：preset_id/preset_label/采样参数/enable_thinking"
        ),
    )
    speak: dict[str, object] = Field(
        default_factory=dict,
        description="数字人播报指令（SSML + 字幕 + 音色），供前端 SDK 播报",
    )
    tools_used: list[dict] = Field(
        default_factory=list,
        description="本轮 Agent 调用的工具记录（名称/参数/结果），供前端展示「已办事」",
    )
    note: str = Field(description="编排方式与运行模式说明")


# ---------- 路由 ----------


@router.get("/presets")
def list_chat_presets() -> dict:
    """模型预设档清单（界面「模型预设」选择器用）。

    同时给出按当前 LLM_MODEL 自动匹配到的档位 id，供前端在「自动」状态下
    展示实际生效的预设。

    合规说明：预设内容全部为项目自写（采样参数取自各模型官方通用建议值），
    未复制任何社区预设的提示词原文。
    """
    settings = get_settings()
    profiles = get_model_profiles()
    matched = resolve_profile(settings.llm_model, profiles)
    return {
        "model": settings.llm_model,
        "auto_preset_id": matched.id,
        "presets": list_presets(profiles),
    }


@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    """一轮完整对话（LangGraph 编排）。"""
    if req.persona_id not in _presets:
        raise HTTPException(status_code=404, detail=f"未知人设：{req.persona_id}")

    settings = get_settings()

    # ① 会话：续聊复用；新聊创建
    session = _repository.get(req.session_id) if req.session_id else None
    if session is None:
        state_vars = {"current_mood": req.current_mood} if req.current_mood else {}
        session = _repository.create(
            persona_id=req.persona_id,
            user_name=req.user_name,
            state_vars=state_vars,
            session_id=req.session_id,
        )
    elif req.current_mood:
        session.set_state_var("current_mood", req.current_mood)

    # ② 组装初始状态（history 为本次输入之前的既有轮次）
    initial_state = {
        "session_id": session.session_id,
        "companion_id": req.persona_id,  # 记忆按陪伴对象（角色）隔离
        "persona_id": req.persona_id,
        "user_name": session.user_name,
        "user_input": req.text,
        "history": list(session.history),
        "turn_index": len(session.history) + 1,
        "state_vars": dict(session.state_vars),
        "style_id": req.style_id or settings.style_preset,
        "preset_id": req.preset_id or "",
        "warnings": [],
    }

    # ③ 执行编排图（召回 → 组装 → 生成+情绪 → 写入）
    result = get_chat_graph().invoke(initial_state)
    reply = result.get("reply", "")
    emotion = result.get("emotion")

    # ④ 情绪回写状态变量：下一轮人设注入 {{current_mood}} 时生效
    if emotion is not None:
        session.set_state_var("current_mood", emotion.label_zh)

    # ⑤ 会话历史追加 user / assistant
    _repository.append_turn(session.session_id, ChatTurn(role="user", text=req.text))
    _repository.append_turn(session.session_id, ChatTurn(role="assistant", text=reply))

    # ⑥ 召回统计
    ctx = result.get("memory_context")
    memory_counts = (
        {
            "memories": len(ctx.memories),
            "facts": len(ctx.facts),
            "summary": 1 if (ctx.summary and ctx.summary.content.strip()) else 0,
        }
        if ctx is not None
        else {}
    )

    system_prompt = result.get("system_prompt", "")
    sampling = result.get("sampling")
    used_style_id = result.get("style_id", "")

    # 数字人播报指令：回复 + 本轮情绪 → SSML（供前端 SDK speak() 播报）
    speak_meta: dict[str, object] = {}
    if settings.avatar_enabled and reply:
        command = build_speak_command(
            reply,
            emotion=emotion.emotion.value if emotion is not None else None,
            intensity=emotion.intensity if emotion is not None else 0.5,
            voice=settings.xmov_voice,
        )
        speak_meta = command.to_dict()

    style_meta: dict[str, object] = {}
    if used_style_id:
        preset = _styles.get(used_style_id)
        style_meta = {
            "style_id": used_style_id,
            "style_name": preset.name if preset else "",
            "examples": result.get("example_count", 0),
            "sampling": sampling.to_provider_kwargs() if sampling else {},
            "profile": sampling.profile_id if sampling else "",
        }

    return ChatResponse(
        session_id=session.session_id,
        persona_id=req.persona_id,
        reply=reply,
        emotion=(
            {
                "label": emotion.emotion.value,
                "label_zh": emotion.label_zh,
                "intensity": emotion.intensity,
                "confidence": emotion.confidence,
                "evidence": emotion.evidence,
                "facial_expression": emotion.facial_expression,
                "source": emotion.source,
            }
            if emotion is not None
            else {}
        ),
        system_prompt=system_prompt,
        messages=result.get("messages", []),
        worldbook_hits=[e.id for e in result.get("worldbook_hits", [])],
        skipped=[e.id for e in result.get("worldbook_skipped", [])],
        memory_counts=memory_counts,
        remembered=result.get("writes", {}),
        warnings=result.get("warnings", []),
        estimated_tokens=estimate_tokens(system_prompt),
        style=style_meta,
        preset=sampling.to_public_dict() if sampling is not None else {},
        speak=speak_meta,
        tools_used=result.get("tools_used", []),
        note=(
            f"LangGraph 编排（6 节点）；模型 {get_llm_provider().name}；"
            f"向量库 {settings.warm_backend}；情绪链路已启用；"
            f"文风 {used_style_id or '未启用'}"
        ),
    )
