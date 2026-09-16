"""把 MCP 工具桥接进 HyPRA 的 ToolRegistry。

桥接后的工具在模型眼里与内置工具完全一致（同一套 OpenAI tools schema），
因此模型可以自然地"办事"：查记忆、记情绪、**调用外部 MCP 能力**。

命名约定：`mcp__<服务器名>__<工具名>`
    - 双下划线分段，避免与内置工具重名（OpenAI 工具名只允许 [a-zA-Z0-9_-]）；
    - 前端可据此识别来源并展示「已调用 XX 服务」。
"""

from __future__ import annotations

from typing import Callable

from app.mcp.session import McpSession, McpToolInfo
from app.tools.registry import ToolRegistry, ToolSpec

#: MCP 工具名前缀
MCP_PREFIX = "mcp"


def mcp_tool_name(server: str, tool: str) -> str:
    """生成桥接后的工具名。"""
    return f"{MCP_PREFIX}__{server}__{tool}"


def is_mcp_tool(name: str) -> bool:
    """判断是否为本项目桥接的 MCP 工具。"""
    return name.startswith(f"{MCP_PREFIX}__")


def describe_for_model(session: McpSession, tool: McpToolInfo) -> str:
    """给模型看的工具描述：标明来源服务器，便于它判断何时该用。"""
    base = tool.description or f"{tool.name}（外部工具）"
    return f"[MCP:{session.config.name}] {base}"


def build_handler(
    session: McpSession, tool: McpToolInfo
) -> Callable[[object, object], dict]:
    """构造 ToolSpec.handler：内部走同步门面调用 MCP 工具。

    参数 args 由 ToolRegistry 传入（本类工具未定义 args_model，故为原始 dict）。
    """

    def handler(args, context) -> dict:  # noqa: ARG001 - 保持与 ToolSpec.handler 签名一致
        arguments = args if isinstance(args, dict) else {}
        text = session.call_tool_sync(tool.name, arguments)
        return {
            "message": text,
            "server": session.config.name,
            "tool": tool.name,
        }

    return handler


def register_mcp_tools(
    registry: ToolRegistry,
    sessions: list[McpSession],
    *,
    logger=None,
) -> list[str]:
    """把已连接会话的工具注册进 registry，返回注册的工具名列表。

    未连接的会话、重名的工具会被跳过（不抛异常，保证启动流程不中断）。
    """
    registered: list[str] = []
    for session in sessions:
        if not session.connected or not session.tools:
            if logger is not None and session.error:
                logger.warning("MCP 服务器「%s」不可用：%s", session.config.name, session.error)
            continue

        for tool in session.tools:
            name = mcp_tool_name(session.config.name, tool.name)
            if registry.get(name) is not None:
                if logger is not None:
                    logger.warning("MCP 工具重名，已跳过：%s", name)
                continue

            registry.register(
                ToolSpec(
                    name=name,
                    description=describe_for_model(session, tool),
                    parameters=tool.input_schema,
                    handler=build_handler(session, tool),
                    args_model=None,  # MCP 工具用 JSON Schema 描述参数，交由模型遵守
                    tags=["mcp", session.config.name],
                )
            )
            registered.append(name)

        if logger is not None and registered:
            logger.info(
                "MCP 服务器「%s」已接入 %d 个工具",
                session.config.name,
                len(registered),
            )

    return registered
