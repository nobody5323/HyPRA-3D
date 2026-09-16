"""MCP 会话管理：应用启动时连接全部服务器，关闭时统一断开。

单例由 `get_mcp_manager()` 提供；依赖可经 `set_mcp_manager()` 替换（测试注入）。

设计立场（沿用项目一贯思路）：
    - 无清单文件 = 未配置 MCP，等同于不启用；
    - 某台服务器连接失败只记录并跳过，**不阻断应用启动**，其余能力照常可用。
"""

from __future__ import annotations

import logging

from app.mcp.bridge import register_mcp_tools
from app.mcp.config import McpServerConfig, load_mcp_servers
from app.mcp.session import McpSession
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class McpManager:
    """持有全部 MCP 会话，负责生命周期与工具注册。"""

    def __init__(
        self,
        *,
        servers_file: str | None = None,
        connect_timeout: float = 20.0,
        call_timeout: float = 30.0,
    ) -> None:
        self.servers_file = servers_file or None
        self.connect_timeout = connect_timeout
        self.call_timeout = call_timeout
        self.sessions: list[McpSession] = []
        self.configs: list[McpServerConfig] = []
        self._started = False

    # ---------- 生命周期 ----------

    def start_all(self) -> None:
        """连接清单里的全部服务器（幂等；失败只告警）。"""
        if self._started:
            return

        self.configs = load_mcp_servers(self.servers_file)
        if not self.configs:
            logger.info("未配置 MCP 服务器（跳过；可用 backend/mcp_servers.json 启用）")
            self._started = True
            return

        for cfg in self.configs:
            session = McpSession(cfg, call_timeout=self.call_timeout)
            ok = session.start(timeout=self.connect_timeout)
            if ok:
                logger.info("MCP 已连接：%s（%d 个工具）", cfg.label, len(session.tools))
            else:
                logger.warning("MCP 连接失败：%s → %s", cfg.label, session.error or "未知原因")
            self.sessions.append(session)

        self._started = True

    def stop_all(self) -> None:
        """断开全部会话（幂等）。"""
        for session in self.sessions:
            try:
                session.stop()
            except Exception as exc:  # noqa: BLE001 - 退出阶段不因清理失败中断
                logger.warning("MCP 断开异常（%s）：%s", session.config.name, exc)
        self.sessions = []
        self._started = False

    # ---------- 工具注册 ----------

    def register_into(self, registry: ToolRegistry) -> list[str]:
        """把已连接服务器的工具注册进 registry（重名自动跳过，可重复调用）。"""
        return register_mcp_tools(registry, self.sessions, logger=logger)

    # ---------- 观测 ----------

    @property
    def connected_count(self) -> int:
        return sum(1 for s in self.sessions if s.connected)

    def status(self) -> list[dict]:
        """状态快照（供 /health 或调试查看）。"""
        return [
            {
                "name": s.config.name,
                "transport": s.config.transport,
                "connected": s.connected,
                "tools": [t.name for t in s.tools],
                "error": s.error,
            }
            for s in self.sessions
        ]


_manager: McpManager | None = None


def get_mcp_manager() -> McpManager:
    """进程内单例。"""
    global _manager
    if _manager is None:
        _manager = McpManager()
    return _manager


def set_mcp_manager(manager: McpManager | None) -> None:
    """替换/重置单例（测试注入用）。"""
    global _manager
    _manager = manager


def configure_manager(*, servers_file: str = "", connect_timeout: float = 20.0,
                      call_timeout: float = 30.0) -> McpManager:
    """按配置创建（并设为）单例。"""
    manager = McpManager(
        servers_file=servers_file or None,
        connect_timeout=connect_timeout,
        call_timeout=call_timeout,
    )
    set_mcp_manager(manager)
    return manager
