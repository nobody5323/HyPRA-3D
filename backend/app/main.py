"""HyPRA 后端入口：FastAPI 应用工厂 + 健康检查。

路由按模块拆分（chat / st_presets / studio / llm / media / knowledge），在此统一注册到 app。
跨域：前端（Next.js，默认 3000）与后端（8000）不同源，必须配置 CORS，
否则浏览器会拦截请求（表现为前端一直显示「后端未连接」）。

生命周期分两段（设计见 `AGENTS.md §9.3`）：
- **静态注册**在 `create_app()`：内置插件写入注册表（只做元数据，无副作用）；
- **动态启停**在 lifespan：发现第三方插件 → setup → start；关闭时逆序 shutdown。
任一插件失败都只告警，**不阻断宿主启动**。
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import media as media_module
from app.api import (
    avatar_models_router,
    chat_router,
    events_router,
    knowledge_router,
    llm_router,
    media_router,
    perception_router,
    plugins_router,
    proactive_router,
    skills_router,
    st_presets_router,
    studio_router,
)
from app.config import cors_origin_list, get_settings, plugin_search_dirs, skills_disabled_list
from app.digital_human.factory import GPT_SOVITS_NAMES
from app.events.bus import get_event_bus
from app.mcp.manager import configure_manager, get_mcp_manager
from app.plugins.builtin import register_all_builtin
from app.plugins.manager import PluginManager, get_plugin_manager, set_plugin_manager
from app.plugins.registry import get_registry, set_registry
from app.proactive.runner import get_proactive_runner
from app.skills.registry import configure_skills, get_skill_registry

logger = logging.getLogger(__name__)


def _startup_tavern_sync() -> None:
    """启动期把酒馆里**新增**的对话增量同步进长期记忆（开关默认关）。

    在后台线程里跑：事实抽取要调模型，首次同步历史会话可能几十秒，
    绝不能拖住启动。失败只记日志——一个外部数据源的同步不该让后端起不来。

    归属用**当前选中的陪伴对象**做回落（启动期没有请求上下文，只能读用户偏好）：
    匹配不到角色卡的会话写到这里，其余按角色各进各的记忆。
    """
    from app.api.chat import current_persona_id, get_memory_store
    from app.memory.tavern_sync import sync_tavern_sessions

    try:
        settings = get_settings()
        memory_store = get_memory_store()
        if memory_store is None:
            logger.warning("酒馆对话自动同步跳过：记忆服务未就绪")
            return
        result, warnings = sync_tavern_sessions(
            memory_store,
            companion_id=current_persona_id(),
            state_path=settings.tavern_import_state_file,
        )
        for warning in warnings:
            logger.warning("酒馆对话自动同步：%s", warning)
        logger.info(
            "酒馆对话自动同步完成：新增 %d 轮（%d 个会话，写入 %d 个陪伴对象）",
            result.turns,
            result.sessions_imported,
            len(result.scopes),
        )
    except Exception as exc:  # noqa: BLE001 - 同步失败不应影响应用运行
        logger.warning("酒馆对话自动同步失败（已忽略）：%s", exc)


def _asr_warmup_needed(settings) -> bool:
    """是否需要预热 ASR（只在用户真的启用了语音识别时才做）。"""
    if not settings.asr_warmup:
        return False
    provider = (settings.asr_provider or "").strip().lower()
    return provider not in ("", "none")


def _warmup_asr() -> None:
    """后台预热 ASR 模型（首次说话不必等十几秒）。

    失败只记日志：预热是优化，不是功能。真正的失败会在首次转写时
    以完整的中文提示暴露给用户（见 `faster_whisper_provider._explain_load_failure`）。
    """
    try:
        from app.api.perception import get_asr_provider

        get_asr_provider().warmup()
    except Exception as exc:  # noqa: BLE001 - 预热失败不影响后端可用
        logger.warning("ASR 预热失败（首次使用时才会再试）：%s", exc)


def create_app() -> FastAPI:
    """应用工厂：创建 FastAPI 实例并注册中间件与路由。"""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ARG001 - FastAPI 约定签名
        """启动：生效插件 + 连 MCP；关闭：逆序释放（任一失败只告警，不阻断启动）。

        注意：插件的**发现**已在 `create_app()` 完成（只读 manifest），
        这里只做**启停**（会执行插件代码）。
        """
        plugin_manager = get_plugin_manager()
        plugin_manager.setup_all()
        plugin_manager.start_all()

        manager = None
        if settings.mcp_enabled:
            manager = configure_manager(
                servers_file=settings.mcp_servers_file,
                connect_timeout=settings.mcp_connect_timeout,
                call_timeout=settings.mcp_call_timeout,
            )
            # 连接涉及子进程与协议握手：放线程里，避免阻塞事件循环
            await asyncio.to_thread(manager.start_all)
            logger.info(
                "MCP 就绪：%d/%d 台服务器已连接",
                manager.connected_count,
                len(manager.configs),
            )
        # 服务端 TTS 预热：放后台线程做（约 1~3s），不阻塞启动；失败只记日志。
        # 只在 gpt_sovits 驱动 + 开启开关时做（其它驱动没有预热能力）。
        if settings.gpt_sovits_warmup and (
            settings.digital_human_provider or ""
        ).strip().lower() in GPT_SOVITS_NAMES:
            app.state.tts_warmup_task = asyncio.create_task(
                asyncio.to_thread(media_module.warmup_digital_human_provider)
            )
        # 酒馆新对话的启动期自动同步（默认关）。
        # 必须放在插件 setup 之后：声明式配置会经 `setup_all` → 运行时覆盖层生效，
        # 在它之前读 `get_settings()` 拿到的还是 .env 里的旧值。
        if get_settings().tavern_sync_on_startup:
            app.state.tavern_sync_task = asyncio.create_task(
                asyncio.to_thread(_startup_tavern_sync)
            )

        # ---- 感知层与主动链路（docs/proactive-multimodal.md）----
        # 事件总线绑定当前循环：绑上之后才支持**跨线程**发布
        # （记忆写入在 `to_thread` 里跑，它可能需要在完成后推一条事件）。
        get_event_bus().bind_loop(asyncio.get_running_loop())

        # ASR 预热：只在**用户已启用**时做。默认 `asr_provider=none` 时不加载模型
        # ——否则每次启动都白占几百 MB 内存，而绝大多数用户从不用语音输入。
        if _asr_warmup_needed(get_settings()):
            app.state.asr_warmup_task = asyncio.create_task(
                asyncio.to_thread(_warmup_asr)
            )

        # 主动链路调度：默认开（见 §5.3「默认开但默认克制」）。
        # 它自己会检查 `proactive_enabled`，因此这里不必再判一次。
        runner = get_proactive_runner()
        runner.start()
        app.state.proactive_runner = runner

        yield

        # 关闭：先停主动链路（它可能正在调 LLM），再断 MCP 与插件
        await runner.stop()
        get_event_bus().unbind_loop()
        plugin_manager.shutdown_all()
        if manager is not None:
            await asyncio.to_thread(manager.stop_all)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # 技能库（§9.6）：与插件体系**独立**——它只是提示词级的方法论，不需要能力面、
    # 不受 core/builtin 分层约束。装载是纯静态读文件，因此同样放在 create_app() 里，
    # 让不走 lifespan 的测试也能看到完整技能列表。
    #
    # ⚠️ 必须排在 register_all_builtin() **之前**：内置工具集里的 `study_skill`
    # 只在存在启用技能时才注册（空库时它是 schema 噪音），而 `_builtin_tools()`
    # 在构造注册对象时就立即读技能库——顺序反了会静默丢失该工具。
    if settings.skills_enabled:
        configure_skills(
            builtin_dir=settings.skills_dir,
            user_dir=settings.user_skills_dir,
            state_path=settings.skills_state_file,
            default_disabled=skills_disabled_list(settings),
        )
    else:
        logger.info("SKILLS_ENABLED=false：跳过技能装载（study_skill 也不会注册）")

    # 插件的**静态阶段**在应用工厂里完成：注册内置插件 + 发现目录插件。
    # 两者都只读 manifest、不执行插件代码，因此即使测试只取 /health、不走 lifespan，
    # 插件列表也是完整的（管理 UI 能看到“已安装但未启用”的插件）。
    set_registry(None)
    plugin_registry = get_registry()
    register_all_builtin(plugin_registry)
    plugin_manager = PluginManager(
        plugin_registry,
        data_dir=Path(settings.plugins_dir).parent,
        # 顺序即优先级：第一方插件先于第三方同名插件（discover 里「内置优先」）；
        # 末尾接上用户自定义的额外来源目录（PLUGIN_EXTRA_DIRS，见 docs/plugin-development.md）。
        plugin_dirs=plugin_search_dirs(settings),
    )
    plugin_manager.discover()
    set_plugin_manager(plugin_manager)

    # 跨域配置（来源可用 CORS_ORIGINS 覆盖；"*" 表示允许全部；
    # CORS_ORIGIN_REGEX 额外放行本机回环地址的任意端口——桌面端与 Web 端端口不固定）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origin_list(settings),
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        """健康检查：服务与配置正常性 + 外部能力（MCP）与插件体系统计。"""
        manager = get_plugin_manager()
        return {
            "status": "ok",
            "app": settings.app_name,
            "mcp": get_mcp_manager().status(),
            "plugins": manager.status(),
            "plugins_summary": manager.summary(),
            "skills": get_skill_registry().status(),
            # 主动链路与推送通道：界面据此显示「会自己开口」的状态，
            # 排查时也先看这里（enabled / running / sent_today / 上次被拦的原因）
            "proactive": get_proactive_runner().status(),
            "events": {"subscribers": get_event_bus().subscriber_count()},
        }

    app.include_router(chat_router)
    app.include_router(knowledge_router)
    app.include_router(llm_router)
    app.include_router(media_router)
    app.include_router(avatar_models_router)
    app.include_router(st_presets_router)
    app.include_router(studio_router)
    app.include_router(plugins_router)
    app.include_router(skills_router)
    # 感知层与主动链路（docs/proactive-multimodal.md）
    app.include_router(events_router)
    app.include_router(perception_router)
    app.include_router(proactive_router)
    return app


app = create_app()
