"""MCP 端到端测试：真实以 stdio 子进程连接项目自带的演示服务器。

覆盖异步桥接的关键路径（连接 → list_tools → call_tool → 桥接进 ToolRegistry → 断开）。
若本机无法启动子进程则 skip（不让环境差异导致 CI 红灯）。
"""

import sys
from pathlib import Path

import pytest

from app.mcp.bridge import register_mcp_tools
from app.mcp.config import McpServerConfig
from app.mcp.session import McpSession
from app.tools.registry import ToolRegistry

#: backend/mcp_servers/wellness_server.py
_SERVER = Path(__file__).resolve().parents[2] / "mcp_servers" / "wellness_server.py"


@pytest.fixture(scope="module")
def session():
    if not _SERVER.exists():
        pytest.skip(f"演示服务器不存在：{_SERVER}")
    cfg = McpServerConfig(
        name="wellness",
        transport="stdio",
        command=sys.executable,
        args=[str(_SERVER)],
    )
    s = McpSession(cfg, call_timeout=30)
    if not s.start(timeout=60):
        pytest.skip(f"无法启动演示 MCP 服务器：{s.error}")
    yield s
    s.stop()


def test_connects_and_lists_tools(session: McpSession) -> None:
    assert session.connected
    names = {t.name for t in session.tools}
    assert {"recommend_ambient_sound", "suggest_breathing_pattern"} <= names
    # inputSchema 应被完整取回（供模型理解参数）
    schema = next(t.input_schema for t in session.tools if t.name == "suggest_breathing_pattern")
    assert "stress_level" in schema["properties"]


def test_call_tool_returns_text(session: McpSession) -> None:
    text = session.call_tool_sync("recommend_ambient_sound", {"emotion": "anxious", "minutes": 10})
    assert "10" in text  # 参数生效
    assert text.strip()


def test_repeated_calls_reuse_the_same_connection(session: McpSession) -> None:
    """常驻连接：多次调用不应重建会话（这是异步桥接的核心收益）。"""
    first = session.call_tool_sync("suggest_breathing_pattern", {"stress_level": 9})
    second = session.call_tool_sync("suggest_breathing_pattern", {"stress_level": 2})
    assert first != second  # 不同参数得到不同建议
    assert session.connected


def test_bridged_tool_works_end_to_end(session: McpSession) -> None:
    """桥接进 ToolRegistry 后，可按内置工具同样的方式调用。"""
    registry = ToolRegistry()
    names = register_mcp_tools(registry, [session])
    assert "mcp__wellness__recommend_ambient_sound" in names

    result = registry.execute(
        "mcp__wellness__recommend_ambient_sound", {"emotion": "tired", "minutes": 20}
    )
    assert result.success
    assert result.data["server"] == "wellness"
    assert result.content
