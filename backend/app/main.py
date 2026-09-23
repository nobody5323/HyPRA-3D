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
    knowledge_router,
    llm_router,
    media_router,
    plugins_router,
    st_presets_router,
    studio_router,
)
from app.config import cors_origin_list, get_settings
from app.digital_human.factory import GPT_SOVITS_NAMES
from app.mcp.manager import configure_manager, get_mcp_manager
from app.plugins.builtin import register_all_builtin
from app.plugins.manager import PluginManager, get_plugin_manager, set_plugin_manager
from app.plugins.registry import get_registry, set_registry

logger = logging.getLogger(__name__)


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
        yield
        plugin_manager.shutdown_all()
        if manager is not None:
            await asyncio.to_thread(manager.stop_all)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # 插件的**静态阶段**在应用工厂里完成：注册内置插件 + 发现目录插件。
    # 两者都只读 manifest、不执行插件代码，因此即使测试只取 /health、不走 lifespan，
    # 插件列表也是完整的（管理 UI 能看到“已安装但未启用”的插件）。
    set_registry(None)
    plugin_registry = get_registry()
    register_all_builtin(plugin_registry)
    plugin_manager = PluginManager(
        plugin_registry,
        data_dir=Path(settings.plugins_dir).parent,
        # 顺序即优先级：第一方插件先于第三方同名插件（discover 里「内置优先」）
        plugin_dirs=[settings.builtin_plugins_dir, settings.plugins_dir],
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
        }

    app.include_router(chat_router)
    app.include_router(knowledge_router)
    app.include_router(llm_router)
    app.include_router(media_router)
    app.include_router(avatar_models_router)
    app.include_router(st_presets_router)
    app.include_router(studio_router)
    app.include_router(plugins_router)
    return app


app = create_app()
