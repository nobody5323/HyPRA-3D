"""HyPRA 后端入口：FastAPI 应用工厂 + 健康检查。

路由按模块拆分（chat / st_presets / studio / llm / media / knowledge），在此统一注册到 app。
跨域：前端（Next.js，默认 3000）与后端（8000）不同源，必须配置 CORS，
否则浏览器会拦截请求（表现为前端一直显示「后端未连接」）。

生命周期：启动时连接 MCP 服务器（把外部工具接入 Agent 行动层），关闭时断开。
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import media as media_module
from app.api import (
    avatar_models_router,
    chat_router,
    knowledge_router,
    llm_router,
    media_router,
    st_presets_router,
    studio_router,
)
from app.config import cors_origin_list, get_settings
from app.digital_human.factory import GPT_SOVITS_NAMES
from app.mcp.manager import configure_manager, get_mcp_manager

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """应用工厂：创建 FastAPI 实例并注册中间件与路由。"""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ARG001 - FastAPI 约定签名
        """启动时连 MCP、关闭时断开（连接失败只告警，不阻断启动）。"""
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
        if manager is not None:
            await asyncio.to_thread(manager.stop_all)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # 跨域配置（来源可用 CORS_ORIGINS 覆盖；"*" 表示允许全部）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origin_list(settings),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        """健康检查：确认服务与配置加载正常，并附带外部能力（MCP）连接状态。"""
        return {
            "status": "ok",
            "app": settings.app_name,
            "mcp": get_mcp_manager().status(),
        }

    app.include_router(chat_router)
    app.include_router(knowledge_router)
    app.include_router(llm_router)
    app.include_router(media_router)
    app.include_router(avatar_models_router)
    app.include_router(st_presets_router)
    app.include_router(studio_router)
    return app


app = create_app()
