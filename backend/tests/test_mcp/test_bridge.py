"""MCP → ToolRegistry 桥接测试。

用假会话（不启动子进程）：验证命名、schema 透传、handler 调用与异常隔离。
"""

from app.mcp.bridge import is_mcp_tool, mcp_tool_name, register_mcp_tools
from app.mcp.config import McpServerConfig
from app.mcp.session import McpToolInfo
from app.tools.registry import ToolRegistry


class _FakeSession:
    """最小可用的假会话（形状与 McpSession 一致）。"""

    def __init__(
        self,
        name: str = "wellness",
        tools: list[McpToolInfo] | None = None,
        result: str = "工具结果",
        error: str = "",
    ) -> None:
        self.config = McpServerConfig(name=name, command="python")
        self.tools = tools if tools is not None else [McpToolInfo(name="t")]
        self.error = error
        self.calls: list[tuple[str, dict]] = []
        self._result = result

    @property
    def connected(self) -> bool:
        return not self.error

    def call_tool_sync(self, name: str, arguments: dict | None = None, **_: object) -> str:
        self.calls.append((name, arguments or {}))
        return self._result


def _tool(name: str = "recommend_ambient_sound", **kwargs) -> McpToolInfo:
    return McpToolInfo(
        name=name,
        description=kwargs.get("description", "按情绪推荐环境音"),
        input_schema=kwargs.get(
            "input_schema",
            {"type": "object", "properties": {"emotion": {"type": "string"}}},
        ),
    )


def test_tool_name_convention() -> None:
    assert mcp_tool_name("wellness", "recommend_ambient_sound") == (
        "mcp__wellness__recommend_ambient_sound"
    )
    assert is_mcp_tool("mcp__wellness__x")
    assert not is_mcp_tool("record_mood_journal")


def test_register_creates_spec_with_source_prefix() -> None:
    registry = ToolRegistry()
    names = register_mcp_tools(registry, [_FakeSession(tools=[_tool()])])

    assert names == ["mcp__wellness__recommend_ambient_sound"]
    spec = registry.get(names[0])
    assert spec is not None
    # 描述标明来源，便于模型判断何时使用
    assert spec.description.startswith("[MCP:wellness]")
    # inputSchema 原样透传给模型
    assert spec.parameters["properties"]["emotion"]["type"] == "string"
    assert spec.tags == ["mcp", "wellness"]
    # 导出的 OpenAI schema 可直接喂模型
    assert spec.to_openai_schema()["function"]["name"] == names[0]


def test_registered_tool_is_executable_through_registry() -> None:
    registry = ToolRegistry()
    session = _FakeSession(result="雨声 + 低频钢琴")
    register_mcp_tools(registry, [session])

    result = registry.execute("mcp__wellness__t", {"emotion": "anxious"})

    assert result.success
    assert result.content == "雨声 + 低频钢琴"
    assert session.calls == [("t", {"emotion": "anxious"})]  # 参数透传
    assert result.data["server"] == "wellness"
    assert result.data["tool"] == "t"


def test_disconnected_session_is_skipped() -> None:
    registry = ToolRegistry()
    assert register_mcp_tools(registry, [_FakeSession(error="连接超时")]) == []
    assert registry.names() == []


def test_duplicate_names_are_skipped_not_crashing() -> None:
    registry = ToolRegistry()
    session = _FakeSession(tools=[_tool()])

    first = register_mcp_tools(registry, [session])
    assert first == ["mcp__wellness__recommend_ambient_sound"]

    # 重复注册（例如 graph 重建）：跳过已有工具，不报错也不产生重复条目
    second = register_mcp_tools(registry, [session])
    assert second == []
    assert len(registry.names()) == 1


def test_call_failure_is_isolated_by_registry() -> None:
    """MCP 调用抛错时必须被 registry 隔离（对话不中断）。"""

    class _BoomSession(_FakeSession):
        def call_tool_sync(self, name, arguments=None, **kwargs):
            raise RuntimeError("连接已断开")

    registry = ToolRegistry()
    register_mcp_tools(registry, [_BoomSession()])

    result = registry.execute("mcp__wellness__t", "{}")

    assert not result.success
    assert "RuntimeError" in result.content
