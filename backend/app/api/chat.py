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

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import get_settings
from app.digital_human.ssml import build_speak_command
from app.graph.chat_graph import build_chat_graph
from app.graph.nodes import ChatNodes
from app.llm.base import LLMProvider
from app.llm.factory import create_llm_provider
from app.llm.profiles import list_presets, load_model_profiles, resolve_profile
from app.llm.runtime import load_runtime_config
from app.memory.cold.extractor import create_extractor
from app.memory.cold.mood_log import SqliteMoodLogStore
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.knowledge.retriever import KnowledgeRetriever
from app.memory.store import MemoryStore
from app.memory.warm.embedding import EmbeddingProvider, create_embedding_provider
from app.memory.warm.factory import create_warm_store
from app.prompts.persona.loader import load_builtin_presets
from app.prompts.renderer import estimate_tokens
# ST 预设的存储与路由已抽到 app/api/st_presets.py；此处导入转发，保持对外入口不变
from app.api.st_presets import get_st_preset_store, set_st_preset_store  # noqa: F401
from app.prompts.style.loader import load_builtin_styles
from app.rag.prompt_manager import PromptManager
from app.session.base import SessionStore
from app.session.context import ChatTurn
from app.session.factory import create_session_store
from app.tools.builtin_tools import build_default_registry
from app.mcp.manager import get_mcp_manager
from app.worldbook.loader import load_builtin_entries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# 进程内单例（内存存储；后续里程碑替换为持久化/独立热层）
# 会话存储（懒加载单例；默认 SQLite，刷新页面 / 重启后端后的历史都在）
_repository: SessionStore | None = None


def get_session_repository() -> SessionStore:
    """懒加载会话存储（测试可经 set_session_repository 注入）。"""
    global _repository
    if _repository is None:
        settings = get_settings()
        # session_db_path 留空则跟随冷层库文件：同库不同表，只需配一处
        _repository = create_session_store(
            settings.session_backend,
            settings.session_db_path or settings.cold_db_path,
        )
    return _repository


def set_session_repository(store: SessionStore | None) -> None:
    """替换/重置会话存储（测试注入用）。"""
    global _repository
    _repository = store
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


def _provider_kwargs() -> dict:
    """构造 provider 的参数：**运行时设置优先**，否则用 .env。

    运行时配置由 `app/api/llm.py` 写入（GET/PUT /llm/config），这里只读——
    两个模块因此没有循环依赖，且「界面切换」与「重启后」的行为天然一致。
    """
    config = load_runtime_config()
    if config is not None:
        return config.provider_kwargs()

    settings = get_settings()
    return {
        "provider": settings.llm_provider,
        "api_key": settings.llm_api_key,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "timeout": settings.llm_timeout,
        "enable_thinking": settings.llm_enable_thinking,
    }


def get_llm_provider() -> LLMProvider:
    """懒加载 LLM provider（默认 mock：无 key 可跑通对话链路）。"""
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = create_llm_provider(**_provider_kwargs())
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
            # 用**当前生效的**模型名匹配预设档（界面可能已切换过模型）
            model_name=getattr(provider, "model", "") or settings.llm_model,
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


# ---------- ST 宏变量（按会话隔离的 {{setvar::}} 存储）----------

#: 宏变量在会话状态变量里的前缀：与人设状态变量（如 current_mood）同表但不同名字空间
_MACRO_VAR_PREFIX = "mv:"


def _read_macro_variables(state_vars: dict[str, str]) -> dict[str, str]:
    """从会话状态变量里取出宏变量（去掉前缀）。"""
    return {
        key[len(_MACRO_VAR_PREFIX):]: value
        for key, value in state_vars.items()
        if key.startswith(_MACRO_VAR_PREFIX)
    }


def _persist_macro_variables(
    session_id: str, before: dict[str, str], after: dict[str, str]
) -> None:
    """把本轮新增/变更的宏变量落库（只写差异，避免每轮全量写）。"""
    changed = {key: value for key, value in after.items() if before.get(key) != value}
    if not changed:
        return
    repository = get_session_repository()
    for key, value in changed.items():
        repository.set_state_var(session_id, _MACRO_VAR_PREFIX + key, value)


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
    st_preset_id: str | None = Field(
        default=None,
        description=(
            "已导入的 SillyTavern 预设 id（可选）。指定后本轮按 ST 预设定组装提示词，"
            "与内置 preset_id 相互独立；见 GET /chat/st-presets"
        ),
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
    knowledge_hits: int = Field(
        default=0,
        description="本轮个人记忆（知识库）召回的文本行数（前端据此展示「用上了你的资料」）",
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
    st_preset: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "本轮 ST 预设（若启用）：st_preset_id/st_preset_name/source_format/"
            "unresolved_macros/used_markers/empty_markers/in_chat_count"
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
    # 用当前生效的模型名匹配：界面切换模型后，「自动」档要跟着换，
    # 否则会把上一个模型的采样档套到新模型上
    model_name = getattr(get_llm_provider(), "model", "") or settings.llm_model
    matched = resolve_profile(model_name, profiles)
    return {
        "model": model_name,
        "auto_preset_id": matched.id,
        "presets": list_presets(profiles),
    }


class SessionSummaryResponse(BaseModel):
    """会话列表项（界面「历史记录」用）。"""

    session_id: str
    persona_id: str
    title: str
    message_count: int
    updated_at: str


class SessionMessage(BaseModel):
    """一条历史消息。"""

    role: str
    text: str
    created_at: str | None = None


class SessionHistoryResponse(BaseModel):
    """一次会话的历史（刷新页面后恢复界面 + 续聊用）。"""

    session_id: str
    persona_id: str
    user_name: str
    messages: list[SessionMessage]


class SessionDeleteResponse(BaseModel):
    """删除一段对话的结果。"""

    session_id: str
    removed_turns: int = Field(description="被删除的消息条数")


@router.get("/sessions", response_model=list[SessionSummaryResponse])
def list_chat_sessions(
    persona_id: str | None = Query(
        default=None, description="只看某个陪伴对象的会话（记忆与会话均按对象隔离）"
    ),
    limit: int = Query(default=20, ge=1, le=100, description="最多返回条数"),
) -> list[SessionSummaryResponse]:
    """会话列表（按最近更新倒序）。"""
    return [
        SessionSummaryResponse(
            session_id=item.session_id,
            persona_id=item.persona_id,
            title=item.title,
            message_count=item.message_count,
            updated_at=item.updated_at,
        )
        for item in get_session_repository().list_sessions(
            persona_id=persona_id, limit=limit
        )
    ]


@router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
def get_chat_session_history(session_id: str) -> SessionHistoryResponse:
    """取一次会话的历史消息。

    返回的是**原始留档**（上限 session_history_limit），不是喂给模型的
    热层窗口——界面刷新后能恢复较长的对话，而模型上下文仍只取最近若干轮。
    """
    settings = get_settings()
    repository = get_session_repository()
    session = repository.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    turns = repository.list_history(session_id, limit=settings.session_history_limit)
    return SessionHistoryResponse(
        session_id=session.session_id,
        persona_id=session.persona_id,
        user_name=session.user_name,
        messages=[
            SessionMessage(role=turn.role, text=turn.text, created_at=turn.created_at)
            for turn in turns
        ],
    )


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
def delete_chat_session(
    session_id: str,
    persona_id: str = Query(..., description="陪伴对象 id（只能删属于它的会话）"),
) -> SessionDeleteResponse:
    """删除一段对话及其全部消息（**不可恢复**）。

    要求 `persona_id` 与会话归属一致：避免误删其它陪伴对象的对话
    （与 POST /chat 的同属校验、界面的按角色隔离保持一致）。
    """
    repository = get_session_repository()
    session = repository.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    if session.persona_id != persona_id:
        raise HTTPException(
            status_code=404,
            detail=f"会话 {session_id} 不属于人设 {persona_id}",
        )
    removed = repository.delete_session(session_id)
    logger.info("删除会话：%s（%d 条消息）", session_id, removed)
    return SessionDeleteResponse(session_id=session_id, removed_turns=removed)


@router.get("/personas")
def list_chat_personas() -> dict:
    """人设（陪伴对象）清单。

    人设 id 同时是**记忆隔离命名空间**（`companion_id`）：情景记忆 / 语义事实 /
    个人记忆都按它分库，所以这份清单也是前端「陪伴对象」选择器的数据源。
    返回 default_persona_id 而不让前端硬编码，避免改预设后界面角色名与实际不符。
    """
    return {
        "default_persona_id": _DEFAULT_PERSONA_ID,
        "personas": [
            {
                "id": preset.id,
                "name": preset.name,
                "title": preset.title,
                "description": preset.description,
                "tags": preset.tags,
            }
            for preset in _presets.values()
        ],
    }


@router.get("/styles")
def list_chat_styles() -> dict:
    """文风预设清单（界面「文风」选择器用）。

    与 persona 同理：预设内容与代码分离，界面从接口取清单，
    新增/调整 presets/*.yaml 无需改动前端代码。
    """
    settings = get_settings()
    return {
        "default_style_id": settings.style_preset,
        "styles": [
            {
                "id": preset.id,
                "name": preset.name,
                "description": preset.description,
                "tags": preset.tags,
                "examples": preset.example_count,
            }
            for preset in _styles.values()
        ],
    }


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, background: BackgroundTasks) -> ChatResponse:
    """一轮完整对话（LangGraph 编排）。"""
    if req.persona_id not in _presets:
        raise HTTPException(status_code=404, detail=f"未知人设：{req.persona_id}")

    settings = get_settings()
    repository = get_session_repository()

    # ① 会话：续聊复用；新聊创建
    session = repository.get(req.session_id) if req.session_id else None
    # 会话必须属于同一个陪伴对象：否则会把 B 角色的轮次写进 A 角色的会话，
    # 破坏「会话按对象隔离」的契约（前端也会因 persona 不符而清掉指针）。
    if session is not None and session.persona_id != req.persona_id:
        raise HTTPException(
            status_code=404,
            detail=f"会话 {req.session_id} 不属于人设 {req.persona_id}",
        )
    if session is None:
        state_vars = {"current_mood": req.current_mood} if req.current_mood else {}
        session = repository.create(
            persona_id=req.persona_id,
            user_name=req.user_name,
            state_vars=state_vars,
            session_id=req.session_id,
        )
    elif req.current_mood:
        # 注意两点：
        # 1) 必须走 repository（而非 session.set_state_var）——后者直接改 dataclass，
        #    SQLite 后端下不会落库；
        # 2) 必须**接住返回值**：SQLite 实现返回的是新对象，本地旧快照不含刚写入的
        #    值，否则本轮 {{current_mood}} 会退化成默认值（并多出一条「未提供」告警）。
        session = repository.set_state_var(
            session.session_id, "current_mood", req.current_mood
        )

    # ①′ 选定的 ST 预设（可选）：不存在/损坏都明确报错，**不静默退回内置路径**
    #     （静默退回会让用户以为预设生效了，最难排查）
    st_preset = None
    st_preset_id = req.st_preset_id or ""
    if st_preset_id:
        try:
            st_preset = get_st_preset_store().load(st_preset_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail=f"未知 ST 预设：{st_preset_id}"
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"ST 预设解析失败：{exc}"
            ) from exc

    # ② 组装初始状态（history 为本次输入之前的既有轮次）
    macro_variables = _read_macro_variables(session.state_vars)
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
        "st_preset_id": st_preset_id,
        "st_preset": st_preset,
        "macro_variables": macro_variables,
        "warnings": [],
    }

    # ③ 执行编排图（召回 → 组装 → 生成）；记忆写入不在此图内
    result = get_chat_graph().invoke(initial_state)
    reply = result.get("reply", "")
    emotion = result.get("emotion")

    # ④′ 宏变量回写（ST 的 {{setvar::}} 结果；只写差异，下一轮能读到）
    _persist_macro_variables(
        session.session_id, macro_variables, result.get("macro_variables") or {}
    )

    # ④ 情绪回写状态变量：下一轮人设注入 {{current_mood}} 时生效
    if emotion is not None:
        # 此处之后的代码不再读 state_vars（下一轮会重新 get），因此无须接住返回值；
        # 与上面 ① 的分支不同：那里紧接着就要用 state_vars 组装提示词
        repository.set_state_var(session.session_id, "current_mood", emotion.label_zh)

    # ⑤ 会话历史追加 user / assistant
    # 两条消息一个事务：分两次写时若中间失败，库里会留下孤立的用户消息
    # （界面有气泡、刷新后却没有回复）
    repository.append_turns(
        session.session_id,
        [
            ChatTurn(role="user", text=req.text),
            ChatTurn(role="assistant", text=reply),
        ],
    )

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
    # 个人记忆（知识库）召回行数：与「情景记忆 / 语义事实」分开统计，
    # 前端需要分别展示各自命中了几条（memory_counts 里没有个人记忆这一层）
    knowledge_hits = len(result.get("knowledge_lines", []))

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

    st_meta: dict[str, object] = {}
    if st_preset_id:
        st_meta = {
            "st_preset_id": st_preset_id,
            "st_preset_name": (
                get_st_preset_store().get_name(st_preset_id) if st_preset else ""
            ),
            **(result.get("st_preset_meta") or {}),
        }

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
        knowledge_hits=knowledge_hits,
        warnings=result.get("warnings", []),
        estimated_tokens=estimate_tokens(system_prompt),
        style=style_meta,
        preset=sampling.to_public_dict() if sampling is not None else {},
        st_preset=st_meta,
        speak=speak_meta,
        tools_used=result.get("tools_used", []),
        note=(
            f"LangGraph 编排（6 节点）；模型 {get_llm_provider().name}；"
            f"向量库 {settings.warm_backend}；情绪链路已启用；"
            f"文风 {used_style_id or '未启用'}；"
            f"预设 {('酒馆预设 ' + st_preset_id) if st_preset_id else '内置分层'}"
        ),
    )
