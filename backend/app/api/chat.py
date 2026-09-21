"""chat 路由（M3：LangGraph 编排）。

POST /chat 的完整链路由 LangGraph 节点图驱动：

    load_persona → worldbook_recall → memory_recall
        → assemble_prompt → generate_reply → write_memory

会话读写在路由层完成（图为无状态编排）：
    ① 取/建会话（携带 history 与状态变量）
    ② graph.invoke 生成回复并写入记忆
    ③ 历史追加 user/assistant 两轮
"""

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException
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
from app.memory.knowledge.retriever import KnowledgeRetriever
from app.memory.store import MemoryStore
from app.memory.warm.embedding import EmbeddingProvider, create_embedding_provider
from app.memory.warm.factory import create_warm_store
from app.prompts.persona.loader import load_builtin_presets
from app.prompts.renderer import estimate_tokens
from app.prompts.style.loader import load_builtin_styles
from app.rag.prompt_manager import PromptManager
from app.session.context import ChatTurn
from app.session.repository import SessionRepository
from app.tools.builtin_tools import build_default_registry
from app.mcp.manager import get_mcp_manager
from app.worldbook.loader import load_builtin_entries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# 进程内单例（内存存储；后续里程碑替换为持久化/独立热层）
_repository = SessionRepository()
_presets = load_builtin_presets()
_entries = load_builtin_entries()
_styles = load_builtin_styles()
_memory_store: MemoryStore | None = None
_embedding_provider: EmbeddingProvider | None = None
_mood_store = None
_llm_provider: LLMProvider | None = None
_chat_graph = None
_model_profiles = None

_DEFAULT_PERSONA_ID = "therapist-elder-sister"


# ---------- 依赖懒加载（测试可经 set_* 注入）----------


def get_embedding_provider() -> EmbeddingProvider:
    """懒加载 embedding provider（温层向量召回 + 世界书语义触发**共用**）。

    两者必须用同一个 provider：否则向量空间不一致，相似度没有意义。
    进程内单例，避免每次编码都重建 SDK 客户端。

    容错：配置有误（如云端 provider 缺 key / 缺 base_url）时降级为本地确定性
    实现并告警——embedding 不可用只应让语义检索退化为字面检索，**不能阻断对话**
    （与 MemoryStore 中温层/冷层异常的降级策略一致）。
    """
    global _embedding_provider
    if _embedding_provider is None:
        settings = get_settings()
        try:
            _embedding_provider = create_embedding_provider(
                settings.embedding_provider,
                api_key=settings.embedding_api_key,
                model=settings.embedding_model,
                base_url=settings.embedding_base_url,
                dimension=settings.embedding_dim,
                timeout=settings.embedding_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 配置错误不应阻断对话
            logger.warning(
                "embedding provider「%s」创建失败，降级为本地确定性实现：%s",
                settings.embedding_provider,
                exc,
            )
            _embedding_provider = create_embedding_provider("deterministic")
    return _embedding_provider


def set_embedding_provider(provider: EmbeddingProvider | None) -> None:
    """替换/重置 embedding provider（测试注入用）。

    换 provider 等于换向量空间：记忆门面与已编译的图必须一起重建。
    """
    global _embedding_provider, _memory_store, _chat_graph
    _embedding_provider = provider
    _memory_store = None
    _chat_graph = None


def _safe_llm_provider() -> LLMProvider | None:
    """给 LLM 抽取器取 provider；不可用时返回 None（抽取器自动退回规则版）。

    LLM 抽取属于「铺上添花」：缺 key 或 provider 构造失败时，记忆功能本身
    不该受影响（与温层/冷层异常的降级策略一致）。
    """
    try:
        return get_llm_provider()
    except Exception as exc:  # noqa: BLE001 - 配置问题不应阻断记忆
        logger.warning("LLM provider 不可用，抽取器退回规则版：%s", exc)
        return None


def _build_knowledge_retriever(settings) -> KnowledgeRetriever | None:
    """构造个人记忆检索器；未启用时返回 None（该层为空，不阻断对话）。

    这里**延迟导入** `app.api.knowledge`：那个模块反过来依赖本模块的
    `get_embedding_provider`，模块级导入会形成循环。
    """
    if not settings.knowledge_enabled:
        return None
    from app.api.knowledge import get_knowledge_store  # noqa: PLC0415

    return KnowledgeRetriever(
        get_knowledge_store(),
        top_k=settings.knowledge_top_k,
        candidate_n=settings.knowledge_candidate_n,
        rrf_k=settings.knowledge_rrf_k,
    )


def get_memory_store() -> MemoryStore:
    """懒加载记忆门面（冷层 SQLite + 温层 memory/qdrant + 抽取器）。"""
    global _memory_store
    if _memory_store is None:
        settings = get_settings()
        cold = SqliteColdStore(db_path=settings.cold_db_path)
        warm = create_warm_store(
            settings.warm_backend,
            provider=get_embedding_provider(),
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
        _memory_store = MemoryStore(
            cold,
            warm,
            fact_limit=settings.memory_fact_limit,
            fact_anchor_n=settings.memory_fact_anchor_n,
            fact_relevant_n=settings.memory_fact_relevant_n,
            memory_top_k=settings.memory_top_k,
            hybrid_enabled=settings.memory_hybrid_enabled,
            candidate_n=settings.memory_candidate_n,
            rrf_k=settings.memory_rrf_k,
            min_similarity=settings.memory_min_similarity,
            half_life_days=settings.memory_half_life_days,
            decay_exponent=settings.memory_decay_exponent,
            emotion_boost=settings.memory_emotion_boost,
            recall_ttl_days=settings.memory_recall_ttl_days,
            extractor=create_extractor(
                settings.memory_extractor, llm_provider=_safe_llm_provider()
            ),
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


def _build_tool_registry():
    """内置工具 + 已连接的 MCP 工具。

    MCP 工具在应用启动（lifespan）时建立连接，此处只做注册；
    注册失败（未配置 / 连接失败）不影响内置工具可用。
    """
    registry = build_default_registry()
    try:
        names = get_mcp_manager().register_into(registry)
        if names:
            logger.info("已接入 %d 个 MCP 工具：%s", len(names), ", ".join(names))
    except Exception as exc:  # noqa: BLE001 - MCP 不可用不应阻断对话能力
        logger.warning("MCP 工具注册失败：%s", exc)
    return registry


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
            knowledge=_build_knowledge_retriever(settings),
            embedding_provider=get_embedding_provider(),
            llm_provider=provider,
            prompt_manager=PromptManager(
                worldbook_budget=settings.worldbook_budget,
                knowledge_budget=settings.knowledge_context_budget,
                memory_budget=settings.memory_layer_budget,
                history_budget=settings.prompt_history_budget,
                total_budget=settings.prompt_total_budget,
                style_budget=settings.prompt_style_budget,
            ),
            styles=_styles,
            default_style_id=settings.style_preset,
            model_name=settings.llm_model,
            profiles=get_model_profiles(),
            tool_registry=_build_tool_registry() if settings.agent_tools_enabled else None,
            mood_store=get_mood_store() if settings.agent_tools_enabled else None,
            max_tool_rounds=settings.max_tool_rounds,
        )
        _chat_graph = build_chat_graph(nodes)
    return _chat_graph


# ---------- 记忆写入：后台执行 ----------

#: 同一陪伴对象的写入串行化。同一会话连续两轮时两次写入不得并发——
#: 事实去重与状态机依赖「读旧事实 → 写新事实」的先后顺序。
_write_locks: dict[str, asyncio.Lock] = {}


def _write_lock(companion_id: str) -> asyncio.Lock:
    lock = _write_locks.get(companion_id)
    if lock is None:
        lock = asyncio.Lock()
        _write_locks[companion_id] = lock
    return lock


async def _write_memory_job(
    companion_id: str,
    user_text: str,
    assistant_text: str,
    *,
    turn_index: int = 0,
    source: str = "",
    subject: str = "用户",
    emotion: str | None = None,
) -> None:
    """后台写入本轮记忆（在响应发出后执行，用户无感知）。

    抽取（LLM 版需 1–3 秒）被移出请求路径；失败只记日志——
    已经发给用户的回复不该因为记忆写入失败而受影响。
    """
    async with _write_lock(companion_id):
        store = get_memory_store()
        try:
            await asyncio.to_thread(
                store.remember_turn,
                companion_id,
                user_text,
                assistant_text,
                turn_index=turn_index,
                source=source,
                subject=subject,
                emotion=emotion,
            )
        except Exception as exc:  # noqa: BLE001 - 后台失败不影响对话
            logger.warning("后台记忆写入失败（已忽略）：%s", exc)


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
        default_factory=dict, description="本轮召回的记忆数：memories/facts"
    )
    memory_scheduled: bool = Field(
        default=False,
        description="本轮记忆写入已提交后台（响应时尚未落库；失败只记日志）",
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
async def chat(req: ChatRequest, background: BackgroundTasks) -> ChatResponse:
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

    # ③ 执行编排图（召回 → 组装 → 生成）；记忆写入不在此图内
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

    # ⑦ 记忆写入提交后台：抽取（LLM 版需 1–3 秒）不应让用户等待。
    #    此处不阻塞响应，失败只记日志（见 _write_memory_job）。
    background.add_task(
        _write_memory_job,
        req.persona_id,
        req.text,
        reply,
        turn_index=initial_state["turn_index"],
        source=session.session_id,
        subject=session.user_name,
        emotion=emotion.emotion.value if emotion is not None else None,
    )

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
        memory_scheduled=True,
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
