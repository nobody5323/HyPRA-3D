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
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
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
from app.memory.trace import record_turn
from app.memory.warm.embedding import (
    CachedEmbeddingProvider,
    EmbeddingProvider,
    create_embedding_provider,
)
from app.memory.warm.factory import create_warm_store
from app.perception.context import render_current
from app.prompts.jailbreak.loader import (
    DEFAULT_PRESET_ID as DEFAULT_JAILBREAK_ID,
    load_builtin_jailbreaks,
)
from app.prompts.jailbreak.models import JailbreakPreset
from app.prompts.persona.loader import PersonaPreset
from app.prompts.renderer import estimate_tokens
# ST 预设的存储与路由已抽到 app/api/st_presets.py；此处导入转发，保持对外入口不变
from app.api.st_presets import get_st_preset_store, set_st_preset_store  # noqa: F401
from app.prompts.style.models import StylePreset
from app.plugins.manager import get_plugin_manager
from app.proactive.prompt import build_proactive_block
from app.proactive.state import get_state_store, local_now
from app.rag.prompt_manager import PromptManager
from app.session.base import SessionStore
from app.session.context import ChatTurn
from app.session.factory import create_session_store
from app.session.mode import MODES, resolve_mode
from app.studio import StudioStore
from app.tools.builtin_tools import build_default_registry
from app.tools.registry import ToolRegistry
from app.mcp.manager import get_mcp_manager
from app.worldbook.models import WorldBookEntry

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


#: 创作工坊存储（用户自建角色卡 / 世界书条目 / 文风预设；懒加载单例）
_studio_store: StudioStore | None = None
#: 外部来源角色卡缓存（酒馆 → persona）；随 `invalidate_chat_graph()` 失效
_tavern_personas_cache: list[PersonaPreset] | None = None
_memory_store: MemoryStore | None = None
_embedding_provider: EmbeddingProvider | None = None
_mood_store = None
_llm_provider: LLMProvider | None = None
_chat_graph = None
#: 叙事框架预设（内置只读，进程内缓存；见 get_jailbreak_presets）
_jailbreak_presets: dict[str, JailbreakPreset] | None = None
_model_profiles = None

#: embedding provider 单例**快照**了这些配置（字名与 `Settings` 字段一致）。
#: 用途：管理 API（`PUT /plugins/{id}/settings`）判断保存后要不要重建它——
#: 重建 embedding 会连带重建记忆门面与已编译的图（世界书向量要重新编码），
#: 所以不能因为「改了 TTS 的音色」就白跑一遍。
EMBEDDING_CONFIG_FIELDS: tuple[str, ...] = (
    "embedding_provider",
    "embedding_api_key",
    "embedding_model",
    "embedding_base_url",
    "embedding_dim",
    "embedding_timeout",
)

#: 本地世界书条目集合**快照**在对话图里（`get_worldbook_entries()` 与兼容向量索引都在
#: `get_chat_graph()` 里一次算好）。酒馆世界书已移到个人知识库检索链路；旧配置字段仍保留，
#: 变化时继续失效图，保证旧客户端更新配置后不会拿到陈旧快照（虽然不再直注入酒馆条目）。
WORLDBOOK_CONFIG_FIELDS: tuple[str, ...] = ("tavern_worldbook_enabled",)

#: 注入预算类字段：`PromptManager` 在图的**构造期**快照了它们
#: （`get_chat_graph()` 里 `PromptManager(worldbook_budget=..., ...)`）。
#: 不失效的表现是「预算改了、注入量一点没变」——得重启才生效。
#: `reply_char_limit`（回复字数上限）同理：它是构造期传入的，改了要重编图。
BUDGET_CONFIG_FIELDS: tuple[str, ...] = (
    "worldbook_budget",
    "knowledge_context_budget",
    "memory_layer_budget",
    "prompt_history_budget",
    "prompt_total_budget",
    "reply_char_limit",
)

_DEFAULT_PERSONA_ID = "therapist-elder-sister"

#: 对话偏好的字段（用户级，控制台 / Web 端 / 桌宠窗共享同一份）
#:
#: `user_name` 是**用户自己的称呼**（对话里 `{{user_name}}` 的取值），不是角色名——
#: 角色名由人设决定。空串 = 没设置过，界面回落到默认「朋友」。
_PREFERENCE_FIELDS = (
    "persona_id",
    "style_id",
    "preset_id",
    "st_preset_id",
    # 叙事框架层（jailbreak）的档位。空串 = 跟随部署默认（JAILBREAK_ENABLED，
    # 出厂为 false 即关闭）；"none" = 用户显式关掉。**用户的选择在这里，
    # 不在 .env**：开关是产品口径，档位是用户偏好。
    "jailbreak_id",
    "mode",
    "user_name",
)

#: 单个偏好值的长度上限（防手改文件或异常入参写入超长字符串）
_MAX_PREFERENCE_LENGTH = 120

#: 用户称呼的长度上限（比通用上限紧：它是一句印在提示词里的称呼，不是整段配置）
_MAX_USER_NAME_LENGTH = 24

# ---------- 依赖懒加载（测试可经 set_* 注入）----------


def get_studio_store() -> StudioStore:
    """懒加载创作工坊存储（用户自建角色 / 世界书条目）。

    目录由 `STUDIO_DIR` 配置（默认 backend/data/studio，已被 .gitignore 覆盖）。
    内置资源（包内 YAML）**只读**，用户内容放用户目录，两者由门面合并。
    同时接上外部只读角色来源（酒馆角色卡 → persona，见 `_tavern_personas`）。
    """
    global _studio_store
    if _studio_store is None:
        _studio_store = StudioStore(
            get_settings().studio_dir,
            external_personas=_tavern_personas,
        )
    return _studio_store


def set_studio_store(store: StudioStore | None) -> None:
    """替换/重置创作工坊存储（测试注入用；换存储同时失效对话图）。"""
    global _studio_store, _chat_graph
    _studio_store = store
    _chat_graph = None


def invalidate_chat_graph() -> None:
    """让已编译的对话图失效：下次对话按最新的角色 / 世界书重建。

    为什么必须显式失效：`ChatNodes` 在构造时**快照**了角色集合与世界书条目
    （世界书还要在构造时一次性编码向量索引）。用户改完内容不失效的话，
    新角色会在路由校验处通过、却在图节点里找不到。

    外部来源的角色卡（酒馆）与条目一样是「构造期快照」，所以一并清缓存
    ——这里就是全仓「对话链路所有快照」的单一失效入口。
    """
    global _chat_graph, _tavern_personas_cache
    _chat_graph = None
    _tavern_personas_cache = None


def get_persona_presets() -> dict[str, PersonaPreset]:
    """当前全部角色卡（内置 + 我的 + 外部来源）的 id 索引，每次取最新。

    坏文件由门面跳过并记警告（一个写坏的用户文件不该让所有人都没法对话）。
    """
    presets, warnings = get_studio_store().all_personas()
    for warning in warnings:
        logger.warning("角色加载：%s", warning)
    return presets


def _tavern_personas() -> list[PersonaPreset]:
    """外部来源的角色卡（酒馆 → persona）；进程内缓存，随对话图一起失效。

    为什么要缓存：`StudioStore` 每次合并清单都会问一次，而 `/chat/personas`
    是界面每次打开都会打的接口——不缓存就是每次请求重读一遍用户目录。
    失效由 `invalidate_chat_graph()` 统一负责（插件启停 / 换数据目录都会走到它）。
    """
    global _tavern_personas_cache
    if _tavern_personas_cache is None:
        from app.prompts.persona.datasource_map import collect_datasource_personas

        presets, warnings = collect_datasource_personas()
        for warning in warnings:
            logger.warning("酒馆角色卡映射：%s", warning)
        if presets:
            logger.info("已接入 %d 张外部角色卡（酒馆）", len(presets))
        _tavern_personas_cache = presets
    return _tavern_personas_cache


def get_worldbook_entries() -> list[WorldBookEntry]:
    """当前用于**直接**世界书注入的条目（内置 + 用户自建）。

    酒馆世界书不再从这里进入对话图：它由
    `app.memory.knowledge.datasource_sync.sync_worldbook_knowledge` 写入个人知识库，
    再由 `knowledge_recall` 做向量 + BM25 检索。这样提示词不会因为整本酒馆世界书
    的常驻/深度条目而被占满；当前轮只会拿到检索命中的少量参考片段。
    """
    entries, warnings = get_studio_store().all_entries()
    for warning in warnings:
        logger.warning("世界书加载：%s", warning)
    return entries


def get_style_presets() -> dict[str, StylePreset]:
    """当前全部文风预设（内置 + 我的）的 id 索引，**每次取最新**。

    这里刻意不再像早期那样在模块导入时 `load_builtin_styles()` 取一份快照：
    那样一来「在工坊里新建一份文风」必须重启后端才会出现在选择器里，
    与角色卡的行为不一致（角色一直是每次取最新的）。改文件与改代码的代价
    不该差一个重启。坏文件由门面跳过并记警告，理由同 `get_persona_presets`。
    """
    styles, warnings = get_studio_store().all_styles()
    for warning in warnings:
        logger.warning("文风加载：%s", warning)
    return styles


def get_jailbreak_presets() -> dict[str, JailbreakPreset]:
    """叙事框架预设（jailbreak）的 id 索引。

    与文风不同，本层目前**只有内置预设**（`app/prompts/jailbreak/presets/`），
    没有接入工坊的自建流程——那一层要处理删除清单、id 与文件名一致性等约定。
    因此这里用进程内缓存：包目录内容不会在运行期变（要改就改文件并重启，
    与 persona/style 的「改文件不必重启」差别在此，等接入工坊后一并放开）。
    """
    global _jailbreak_presets
    if _jailbreak_presets is None:
        _jailbreak_presets = load_builtin_jailbreaks()
    return _jailbreak_presets


def get_embedding_provider() -> EmbeddingProvider:
    """懒加载 embedding provider（温层向量召回 + 世界书语义触发**共用**）。

    两者必须用同一个 provider：否则向量空间不一致，相似度没有意义。
    进程内单例，避免每次编码都重建 SDK 客户端。

    容错：配置有误（如云端 provider 缺 key / 缺 base_url）时降级为本地确定性
    实现并告警——embedding 不可用只应让语义检索退化为字面检索，**不能阻断对话**
    （与 MemoryStore 中温层/冷层异常的降级策略一致）。

    统一套一层 `CachedEmbeddingProvider`：一轮对话会对同一句用户输入发起
    4–7 次编码请求（世界书 1 + 知识库每作用域 1 + 温层 1），且串行执行。
    远程 embedding 单次 200–500ms，不缓存就是白等 1–3 秒。缓存随 provider
    一起重建，所以改 embedding 配置不会串味到旧向量空间。
    """
    global _embedding_provider
    if _embedding_provider is None:
        settings = get_settings()
        try:
            inner = create_embedding_provider(
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
            inner = create_embedding_provider("deterministic")
        _embedding_provider = CachedEmbeddingProvider(
            inner, max_entries=settings.embedding_cache_size
        )
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
    """内置工具 + 已连接的 MCP 工具 + 插件贡献的工具。

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
    _register_plugin_tools(registry)
    return registry


def _register_plugin_tools(registry: ToolRegistry) -> None:
    """把**启用插件**贡献的工具并入注册表（`AGENTS.md §9.4` 的 `tool` 能力面）。

    少了这一步，插件的 `tool` 能力就只是个摆设：`ToolSpec` 静静躺在
    `PluginRegistry` 的聚合视图里，模型既看不到也调不到。

    **宿主优先**：内置与 MCP 工具先注册，重名的插件工具跳过并告警。重名是**预期**
    而非异常——`tools-builtin` 插件贡献的就是 `build_default_registry()` 那 4 个工具
    （它同时是「能力账本」，见 `AGENTS.md §9.11` 的 P2 补充），真正由插件带来的增量
    是第三方插件与 `tavern-bridge` 这类目录插件。
    """
    try:
        contributions = get_plugin_manager().registry.tools()
    except Exception as exc:  # noqa: BLE001 - 插件不可用不应阻断对话能力
        logger.warning("插件工具读取失败：%s", exc)
        return

    for spec in contributions:
        if registry.get(spec.name) is not None:
            logger.debug("插件工具与已有工具重名，跳过：%s", spec.name)
            continue
        registry.register(spec)
        logger.info("已接入插件工具：%s", spec.name)


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
            presets=get_persona_presets(),
            entries=get_worldbook_entries(),
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
                # 感知层预算：行踪与画像进来之后默认值上调到 200（见 config.py）
                perception_budget=settings.perception_budget,
                # 单次回复字数上限（产品口径：像打电话，不像念小说段落）
                reply_char_limit=settings.reply_char_limit,
            ),
            styles=get_style_presets(),
            default_style_id=settings.style_preset,
            # 叙事框架层（jailbreak）：**出厂默认关闭**。
            # 这里烘进去的是「直接调图」（测试 / 脚本）时的兜底默认；
            # 走 HTTP 的请求由路由按「请求 > 部署开关」逐轮解析后放进 state，
            # 因此界面上开关这一层**不必重建图、也不必重启**。
            jailbreaks=get_jailbreak_presets(),
            default_jailbreak_id=(
                (settings.jailbreak_preset or DEFAULT_JAILBREAK_ID)
                if settings.jailbreak_enabled
                else ""
            ),
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
    jailbreak_id: str | None = Field(
        default=None,
        description=(
            "叙事框架（jailbreak）预设 id（可选）。缺省用用户偏好 / 部署默认，"
            "而出厂默认是**关闭**；传具体 id 即本轮开启该档，传 \"none\" 即本轮强制关闭。"
            "见 GET /chat/jailbreak-presets"
        ),
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
    mode: str | None = Field(
        default=None,
        description=(
            "交互模式（可选）：companion（桌宠对话，默认）| tavern（酒馆聊天）。"
            "会话级属性——不传时由 st_preset_id 推断（选了 ST 预设即 tavern），"
            "再回落到会话既有值；见 app/session/mode.py"
        ),
    )
    proactive: bool = Field(
        default=False,
        description=(
            "本轮是否为**主动开口**（由 app/proactive/runner.py 合成，前端不传）。"
            "为 true 时：不写 user 消息进会话历史、system 追加主动开口的行为约束块。"
            "见 docs/proactive-multimodal.md §5.4"
        ),
    )
    proactive_reason: str = Field(
        default="",
        description="主动开口的触发原因（内部信息，仅用于日志与调试，不进提示词正文）",
    )


class ChatResponse(BaseModel):
    """一轮对话的完整结果。"""

    session_id: str
    persona_id: str
    mode: str = Field(
        default="companion",
        description=(
            "本轮实际生效的交互模式（companion 桌宠 / tavern 酒馆）。"
            "界面据此解释「这轮为什么没有酒馆世界书」——酒馆来源知识只在 tavern 模式召回"
        ),
    )
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
    jailbreak: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "本轮叙事框架层（jailbreak，若启用）：jailbreak_id/jailbreak_name/"
            "intensity/requires_adult。**默认为空 dict**（出厂关闭）"
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
    llm_rounds: int = Field(
        default=0,
        description=(
            "本轮 LLM 实际调用次数（工具循环 + 强制情绪补调 + 空回复重试）。"
            "排查「回复慢」的首选指标：每多一次调用就多付一遍完整的 prefill + decode"
        ),
    )
    timings: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "分阶段耗时（毫秒）：session_setup / 各召回与组装节点 / graph_total / "
            "post_graph / route_total。用于定位瓶颈在哪一段"
        ),
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


class SessionPurgeResponse(BaseModel):
    """清空某个陪伴对象全部会话的结果。"""

    persona_id: str
    removed_sessions: int = Field(description="被清空的会话段数")
    removed_turns: int = Field(description="被清空的消息条数")


class MemoryPurgeResponse(BaseModel):
    """清空某个陪伴对象全部记忆的结果。"""

    persona_id: str
    removed: dict[str, int] = Field(
        description="各层被删除的条数：warm（情景记忆）/ facts（语义事实）/ mood_log（情绪日记）/ knowledge（个人记忆分块）"
    )


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


@router.delete("/sessions", response_model=SessionPurgeResponse)
def purge_chat_sessions(
    persona_id: str = Query(..., description="清空该陪伴对象的全部会话"),
) -> SessionPurgeResponse:
    """清空某个陪伴对象的**全部**会话（**不可恢复**）。

    为什么要这个接口：会话是「每轮对话都可能新建」的对象——试聊、超时重试、
    空回复都会各留一段，标题又都是首条用户消息（同一句话于是看着一模一样），
    几十段一条条删不现实。

    记忆不受影响：情景记忆（温层）/ 语义事实（冷层）按陪伴对象跨会话累积，
    删会话不会连带删除记忆；要彻底忘掉某段内容，那是另一个入口。
    该角色没有任何会话时返回 0（幂等，不算错误）。
    """
    sessions, turns = get_session_repository().delete_sessions(persona_id)
    logger.info("清空会话：%s（%d 段 / %d 条消息）", persona_id, sessions, turns)
    return SessionPurgeResponse(
        persona_id=persona_id, removed_sessions=sessions, removed_turns=turns
    )


def purge_persona_memory(persona_id: str) -> dict[str, int]:
    """清空某个陪伴对象的**四层记忆**，返回各层删除计数。

    四层：情景记忆（温层）/ 语义事实（冷层）/ 情绪日记 / 个人记忆（知识库分块）。
    只清一层会留下「它还认得我」的残留，比不清更难解释。

    各层没有数据时返回 0（幂等）——清空一个已经空的库不是错误。
    某一层失败（如向量库连不上）只记日志并计 0，不回滚其它层：
    已经删掉的部分不可能恢复，谎报全成功更糟。

    抽成函数而不是写在路由里：创作工坊「删除角色卡」要级联同一套清理
    （`DELETE /chat/studio/personas/{id}`），两处各写一遍迟早漏掉一层。
    """
    # 延迟导入：knowledge 模块反过来依赖本模块的 get_embedding_provider
    from app.api.knowledge import get_knowledge_store  # noqa: PLC0415

    removed = dict(get_memory_store().purge_scope(persona_id))

    try:
        removed["mood_log"] = get_mood_store().clear_scope(persona_id)
    except Exception as exc:  # noqa: BLE001 - 单层失败不阻断其余层
        logger.warning("清空情绪日记失败（%s）：%s", persona_id, exc)
        removed["mood_log"] = 0

    try:
        removed["knowledge"] = get_knowledge_store().clear_scope(persona_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("清空个人记忆失败（%s）：%s", persona_id, exc)
        removed["knowledge"] = 0

    return removed


@router.delete("/memory", response_model=MemoryPurgeResponse)
def purge_memory(
    persona_id: str = Query(..., description="清空该陪伴对象的全部记忆"),
) -> MemoryPurgeResponse:
    """清空某个陪伴对象的**全部记忆**（**不可恢复**）。

    四层一起清：情景记忆（温层）/ 语义事实（冷层）/ 情绪日记 / 个人记忆（知识库）。

    **刻意不动会话记录**：历史对话是另一件事（`DELETE /chat/sessions`）。
    两个入口分开，用户才能「只清记忆、留着聊天记录」或反之。
    反过来，创作工坊删除角色卡会**同时**清会话与记忆
    （`DELETE /chat/studio/personas/{id}`）——那是「删掉这个角色」的一个决定。
    """
    removed = purge_persona_memory(persona_id)
    logger.info("清空记忆：%s（%s）", persona_id, removed)
    return MemoryPurgeResponse(persona_id=persona_id, removed=removed)


@router.get("/personas")
def list_chat_personas() -> dict:
    """人设（陪伴对象）清单。

    人设 id 同时是**记忆隔离命名空间**（`companion_id`）：情景记忆 / 语义事实 /
    个人记忆都按它分库，所以这份清单也是前端「陪伴对象」选择器的数据源。
    返回 default_persona_id 而不让前端硬编码，避免改预设后界面角色名与实际不符。

    清单包含**用户自建角色**（内置只读 + 我的可编辑，两者在对话链路上无差别），
    因此用户新建角色后这里立刻可见，无需重启后端。
    """
    summaries, warnings = get_studio_store().list_personas()
    for warning in warnings:
        logger.warning("角色清单：%s", warning)
    return {
        "default_persona_id": _DEFAULT_PERSONA_ID,
        "personas": [item.as_dict() for item in summaries],
    }


@router.get("/styles")
def list_chat_styles() -> dict:
    """文风预设清单（界面「文风」选择器用）。

    与 persona 同理：预设内容与代码分离，界面从接口取清单，
    新增/调整 presets/*.yaml **或在工坊里自建一份**都无需改动前端代码，
    也不必重启后端（清单每次请求现取，见 `get_style_presets`）。
    """
    settings = get_settings()
    styles = get_style_presets()
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
            for preset in styles.values()
        ],
    }


@router.get("/jailbreak-presets")
def list_jailbreak_presets() -> dict:
    """叙事框架（jailbreak）预设清单 + 当前是否启用。

    界面据此渲染开关与档位选择器。**默认关闭**是产品口径，不是待办：
    本层会改写模型的回应框架，属于用户知情后自行开启的能力。

    响应里的 `enabled` 是**部署默认**（`JAILBREAK_ENABLED`）；用户自己的选择在
    `GET /chat/preferences` 的 `jailbreak_id` 里，界面应以后者为准回填。

    ⚠️ `requires_adult` 为 true 的档位只应展示给已确认成年的用户——
    后端不拦截（本字段只做声明），确认动作由前端/部署方负责。
    """
    settings = get_settings()
    presets = get_jailbreak_presets()
    return {
        "enabled": settings.jailbreak_enabled,
        "default_jailbreak_id": settings.jailbreak_preset or DEFAULT_JAILBREAK_ID,
        "presets": [
            {
                "id": preset.id,
                "name": preset.name,
                "description": preset.description,
                "tags": preset.tags,
                "intensity": preset.intensity,
                "intensity_label": preset.intensity_label,
                "requires_adult": preset.requires_adult,
            }
            for preset in presets.values()
        ],
    }


# =============================================================
# 对话偏好（人设 / 文风 / 提示词预设 / 酒馆预设 / 用户称呼）
# =============================================================
#
# 为什么存后端：三个界面的 localStorage 互不相通（控制台与桌宠窗是
# 127.0.0.1:34567，Web 端是 localhost:3000），而「用哪套提示词」「我该怎么被称呼」
# 必须是三者一致的事实。
#
# 语义：存的是**用户最后一次的选择**，空串 = 没选过（界面回落到部署默认）。
# 所以它不会覆盖部署配置，只是比部署配置优先。


def _preferences_path() -> Path:
    return Path(get_settings().chat_preferences_path)


def _read_preferences() -> dict[str, str]:
    """读偏好；文件缺失 / 损坏一律按「全部未设置」处理。

    不能因为一个被改坏的 JSON 就让对话起不来——偏好只是「上次选了哪套提示词」，
    丢了顶多是回到部署默认。
    """
    empty = {field: "" for field in _PREFERENCE_FIELDS}
    path = _preferences_path()
    if not path.is_file():
        return empty

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("对话偏好文件读不动，按未设置处理：%s", path)
        return empty

    if not isinstance(raw, dict):
        return empty

    return {field: str(raw.get(field) or "").strip() for field in _PREFERENCE_FIELDS}


def _write_preferences(values: dict[str, str]) -> None:
    """原子写：先写临时文件再 rename，避免留下半截 JSON。"""
    path = _preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _known_persona_ids() -> set[str]:
    """内置人设 + 用户自建角色的 id。

    人设 id 同时是**记忆隔离命名空间**（`companion:{id}`），写一个不存在的 id
    不只是「这轮回复不对」——它会让记忆写进另一个库。所以只有它做存在性校验，
    文风 / 预设写错顶多是那一轮回落默认。
    """
    ids = set(get_persona_presets())
    summaries, _warnings = get_studio_store().list_personas()
    ids.update(item.id for item in summaries)
    return ids


class ChatPreferencesPatch(BaseModel):
    """偏好补丁：只改传进来的字段（不传 = 保持原值，空串 = 清除）。"""

    persona_id: str | None = None
    style_id: str | None = None
    preset_id: str | None = None
    st_preset_id: str | None = None
    # 叙事框架（jailbreak）：空串 = 跟随部署默认（出厂为关闭）；
    # "none" = 用户显式关掉；具体 id = 开启该档。开关的**用户选择**落在偏好文件里。
    jailbreak_id: str | None = None
    mode: str | None = None
    user_name: str | None = None


@router.get("/preferences")
def get_chat_preferences() -> dict:
    """当前对话偏好（空串 = 未设置，由界面回落到部署默认）。"""
    return _read_preferences()


def current_persona_id() -> str:
    """当前选中的陪伴对象 id（未设置过则用部署默认）。

    给**非请求上下文**的调用方用（启动期自动同步、状态接口）：它们拿不到
    前端传的 persona_id，只能读用户偏好。规则与界面一致（偏好 > 部署默认）。
    """
    return _read_preferences().get("persona_id") or _DEFAULT_PERSONA_ID


@router.put("/preferences")
def put_chat_preferences(payload: ChatPreferencesPatch) -> dict:
    """更新对话偏好（控制台 / Web 端 / 桌宠窗共用同一份）。"""
    current = _read_preferences()
    sent = payload.model_fields_set

    for field in _PREFERENCE_FIELDS:
        if field not in sent:
            continue

        value = str(getattr(payload, field) or "").strip()
        limit = _MAX_USER_NAME_LENGTH if field == "user_name" else _MAX_PREFERENCE_LENGTH
        if len(value) > limit:
            raise HTTPException(
                status_code=400, detail=f"{field} 过长（上限 {limit} 字符）"
            )

        if field == "persona_id" and value and value not in _known_persona_ids():
            raise HTTPException(status_code=400, detail=f"未知人设：{value}")

        # 模式是**闭集合**：写错的话 `normalize_mode` 会静默回落成桌宠模式，
        # 用户看到的是「切了酒馆模式却没生效」——所以这里直接拒绝，让错误可见。
        if field == "mode" and value and value not in MODES:
            raise HTTPException(
                status_code=400, detail=f"未知交互模式：{value}（可选：{', '.join(sorted(MODES))}）"
            )

        current[field] = value

    _write_preferences(current)
    return current


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, background: BackgroundTasks) -> ChatResponse:
    """一轮完整对话（LangGraph 编排）。

    **主动链路也走这里**：`app/proactive/runner.py` 合成一个内部请求后调用
    `execute_turn`——刻意不做第二条生成路径（另写一套 = 人格漂移 + 两处维护）。
    见 `docs/proactive-multimodal.md` §5.4。
    """
    return await execute_turn(req, background)


#: 后台任务的**强引用集合**。
#:
#: `asyncio` 只保证「有强引用」的任务不被 GC 回收——直接 `create_task(...)`
#: 而不接住返回值，任务可能在中途被回收（官方文档明确警告过）。
#: 主动链路没有请求上下文、拿不到 `BackgroundTasks`，因此需要这条兜底路径。
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    """把协程挂成后台任务并持有引用。"""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def execute_turn(
    req: ChatRequest, background: BackgroundTasks | None = None
) -> ChatResponse:
    """执行一轮对话（HTTP 路由与主动链路共用）。

    参数:
        background: FastAPI 的 `BackgroundTasks`。主动链路传 None——
            此时记忆写入改为挂一个后台 asyncio 任务（语义等价：都不阻塞响应）。
    """
    # 路由级计时锚点：与图内的节点耗时一起，把「这一轮到底慢在哪」说清楚
    started = time.perf_counter()
    if req.persona_id not in get_persona_presets():
        # 404 而不是静默回落到默认人设：人设 id 同时是记忆隔离命名空间，
        # 悄悄换一个就等于把这轮对话写进另一个角色的记忆库。
        # 但要把「为什么」和「怎么办」写进 detail——前端会原样展示给用户。
        raise HTTPException(
            status_code=404,
            detail=(
                f"未知人设：{req.persona_id}。"
                "这个角色可能已被删除，或是在别的界面（控制台 / 桌宠窗）里创建后"
                "当前界面还没刷新——请在「陪伴对象」里重新选一个再发送。"
            ),
        )

    settings = get_settings()

    # ①′ 感知·此刻：把当前全部感知事实（时钟 / 快照 / 行踪 / 画像）渲染成提示词片段。
    #     **在路由层算、放进 state**：图节点就不必知道感知快照这个全局单例的存在，
    #     单测可以直接构造 state 来验证组装结果。无感知源时它是空串 → 该层零开销。
    #     汇集与渲染走 `render_current` 一个入口（见 app/perception/gather.py）——
    #     拆成两步调用时极易漏掉「先汇集」，而漏掉的表现是静默地什么都不注入。
    perception_text = render_current(settings=settings)

    # ①″ 主动开口的行为约束块：**只有主动轮非空**，普通轮次该层完全不产生字符。
    #     字数上限刻意走提示词而不是 PromptManager 的构造参数——后者是编译期快照，
    #     为一个逐轮变化的值重建整张图（世界书还要重新编码向量）不值得。
    proactive_text = (
        build_proactive_block(
            reason=req.proactive_reason,
            char_limit=settings.proactive_reply_char_limit,
        )
        if req.proactive
        else ""
    )

    # ①‴ 记录「用户说话了」：主动链路的闸门⑤（用户正在对话中则不插话）靠它。
    #     只在**真实用户轮**记录——主动轮记进去等于告诉闸门「用户刚说过话」，
    #     结果是主动开口自己把自己锁死。
    if not req.proactive:
        try:
            get_state_store(settings.proactive_state_path).note_user_message(local_now())
        except Exception as exc:  # noqa: BLE001 - 节流记录失败不该影响对话
            logger.warning("记录用户消息时刻失败（已忽略）：%s", exc)

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
    # ①′ 交互模式（会话级属性，见 app/session/mode.py）。
    #     必须在建会话之前定出来——mode 是会话字段，创建时就写进去，之后不再变。
    mode = resolve_mode(
        explicit=req.mode,
        session_mode=session.mode if session is not None else None,
        st_preset_id=req.st_preset_id,
    )

    if session is None:
        state_vars = {"current_mood": req.current_mood} if req.current_mood else {}
        session = repository.create(
            persona_id=req.persona_id,
            mode=mode,
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

    # ①″ 选定的 ST 预设（可选）：不存在/损坏都明确报错，**不静默退回内置路径**
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
        "mode": mode,                    # 交互模式：决定是否召回酒馆世界书
        "user_name": session.user_name,
        "user_input": req.text,
        "history": list(session.history),
        "turn_index": len(session.history) + 1,
        "state_vars": dict(session.state_vars),
        "style_id": req.style_id or settings.style_preset,
        # 叙事框架层：**逐轮解析**，不吃 get_chat_graph() 的缓存默认值——
        # 那样在界面上开关这一层就要重启后端，与「文风/人设随选随生效」不一致。
        # 优先级：请求显式指定 > 部署默认（JAILBREAK_ENABLED）。出厂关闭 → 空串。
        "jailbreak_id": (
            req.jailbreak_id
            if req.jailbreak_id is not None
            else (
                (settings.jailbreak_preset or DEFAULT_JAILBREAK_ID)
                if settings.jailbreak_enabled
                else ""
            )
        ),
        "preset_id": req.preset_id or "",
        "st_preset_id": st_preset_id,
        "st_preset": st_preset,
        "macro_variables": macro_variables,
        # 感知·此刻（已渲染的 `- …` 行）与主动开口约束块。
        # 两者默认都是空串，因此**不改变任何既有轮次的组装结果**
        # （有回归测试守着，见 tests/test_rag/test_prompt_manager_perception.py）。
        "perception_text": perception_text,
        "proactive": req.proactive,
        "proactive_reason": req.proactive_reason,
        "proactive_text": proactive_text,
        "warnings": [],
    }

    # ③ 执行编排图（召回 → 组装 → 生成）；记忆写入不在此图内
    #
    # 必须丢到工作线程：图里是**同步的多次网络 IO**（embedding / Qdrant / LLM），
    # 单轮数秒。本路由是 `async def`，直接同步调用会占住 asyncio 事件循环——
    # 后果不只是这一个请求慢：同一进程内的后台记忆写入任务、前端并发的
    # `/media/speak` 分段请求、第二个窗口的对话全都被排在它后面（表现为
    # 「一边说话，另一边整个卡住」）。`run_in_threadpool` 把阻塞段移出循环，
    # 事件循环在等待期间仍可调度其它协程。
    setup_ms = (time.perf_counter() - started) * 1000.0
    graph_started = time.perf_counter()
    result = await run_in_threadpool(get_chat_graph().invoke, initial_state)
    graph_ms = (time.perf_counter() - graph_started) * 1000.0
    post_graph_started = time.perf_counter()
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
    #
    # 空回复（模型把 token 全花在思考上、content 为空）**不写空 assistant 消息**：
    # 那种回合会留下「标题就是用户那句话、点开只有一条空气泡」的垃圾会话，
    # 攒多了列表里全是同名条目（实测出现过 17 个「你好，在吗？」）。
    # 用户消息照常落库——用户说过的话不该丢，重试同一会话就能拿到回复；
    # 两条消息仍然一个事务（分两次写时中途失败会留下孤立的用户消息）。
    #
    # 主动轮**不写 user 消息**：那条「[系统触发·非用户输入]」是内部指令，
    # 不是用户说的话。写进去会让历史里出现一句用户从没说过的话，
    # 下一轮还会作为「用户说过」被注入——人格与事实都被污染。
    turns: list[ChatTurn] = [] if req.proactive else [ChatTurn(role="user", text=req.text)]
    if reply:
        turns.append(ChatTurn(role="assistant", text=reply))
    if turns:
        repository.append_turns(session.session_id, turns)

    # ⑤′ 注入留痕：这一轮到底给模型看了什么。
    #
    # 「它怎么知道我聊过这个？」这类问题事后无法复现，而 system_prompt 只随响应
    # 回给前端、不落盘，重启即无从考证——所以把召回行与提示词指纹写进 JSONL，
    # 下次直接 grep 角色 id 就能看到那一轮的注入内容（见 app/memory/trace.py）。
    record_turn(
        settings.memory_trace_path,
        session_id=session.session_id,
        companion_id=req.persona_id,
        user_input=req.text,
        mode=mode,
        worldbook_ids=[entry.id for entry in (result.get("worldbook_hits") or [])],
        warm_lines=list(result.get("warm_lines") or []),
        fact_lines=list(result.get("fact_lines") or []),
        knowledge_lines=list(result.get("knowledge_lines") or []),
        system_prompt=result.get("system_prompt", ""),
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
        preset = get_style_presets().get(used_style_id)
        style_meta = {
            "style_id": used_style_id,
            "style_name": preset.name if preset else "",
            "examples": result.get("example_count", 0),
            "sampling": sampling.to_provider_kwargs() if sampling else {},
            "profile": sampling.profile_id if sampling else "",
        }

    # 叙事框架层：**为空即关闭**，前端据此判断「本轮有没有开破限」
    # （不要用 settings.jailbreak_enabled 判断：那只是部署默认，用户可在单轮覆盖）
    jailbreak_meta: dict[str, object] = {}
    used_jailbreak_id = result.get("jailbreak_id", "")
    if used_jailbreak_id:
        jb_preset = get_jailbreak_presets().get(used_jailbreak_id)
        jailbreak_meta = {
            "jailbreak_id": used_jailbreak_id,
            "jailbreak_name": jb_preset.name if jb_preset else "",
            "intensity": jb_preset.intensity if jb_preset else 0,
            "requires_adult": jb_preset.requires_adult if jb_preset else False,
        }

    # ⑦ 记忆写入提交后台：抽取（LLM 版需 1–3 秒）不应让用户等待。
    #    此处不阻塞响应，失败只记日志（见 _write_memory_job）。
    #
    # 主动轮的 `user_text` 传**空串**：那条「[系统触发·非用户输入]」是内部指令，
    # 写进记忆等于让角色「记得用户说过一句他从没说过的话」。
    # assistant 那一侧照常写入——主动开口是真实发生过的对话（§5.4）。
    memory_kwargs = dict(
        companion_id=req.persona_id,
        user_text="" if req.proactive else req.text,
        assistant_text=reply,
        turn_index=initial_state["turn_index"],
        source=session.session_id,
        subject=session.user_name,
        emotion=emotion.emotion.value if emotion is not None else None,
    )
    if background is not None:
        background.add_task(_write_memory_job, **memory_kwargs)
    else:
        _spawn(_write_memory_job(**memory_kwargs))

    # ⑦ 耗时汇总：图内各节点（由 build_chat_graph 计时）+ 路由三段。
    #
    # 为什么要一并回给前端而不只写日志：日志要人去翻，而这个数字是「刚才那句
    # 为什么等了这么久」的唯一客观答案。排查时先看 generate_reply 的占比——
    # 它是 LLM 生成，只能靠调模型/减调用次数；若 worldbook_recall 这类召回节点
    # 反而占大头，那问题在 embedding / 向量库，方向完全不同。
    timings: dict[str, float] = {
        **(result.get("timings") or {}),
        "session_setup": round(setup_ms, 1),
        "graph_total": round(graph_ms, 1),
        "post_graph": round((time.perf_counter() - post_graph_started) * 1000.0, 1),
        "route_total": round((time.perf_counter() - started) * 1000.0, 1),
    }
    llm_rounds = int(result.get("llm_rounds") or 0)
    logger.info(
        "对话耗时 %.0fms（LLM %d 次）：%s",
        timings["route_total"],
        llm_rounds,
        " | ".join(f"{key} {value:.0f}ms" for key, value in timings.items()),
    )

    return ChatResponse(
        session_id=session.session_id,
        persona_id=req.persona_id,
        mode=mode,
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
        jailbreak=jailbreak_meta,
        speak=speak_meta,
        tools_used=result.get("tools_used", []),
        llm_rounds=llm_rounds,
        timings=timings,
        note=(
            f"LangGraph 编排（6 节点）；模型 {get_llm_provider().name}；"
            f"向量库 {settings.warm_backend}；情绪链路已启用；"
            f"文风 {used_style_id or '未启用'}；"
            f"叙事框架 {used_jailbreak_id or '未启用'}；"
            f"预设 {('酒馆预设 ' + st_preset_id) if st_preset_id else '内置分层'}"
        ),
    )
