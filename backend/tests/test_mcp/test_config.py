"""MCP 服务器清单解析测试（纯函数，不启动任何进程）。"""

import json
import sys
from pathlib import Path

from app.mcp.config import McpServerConfig, expand_path_placeholders, load_mcp_servers


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_missing_file_means_not_configured(tmp_path: Path) -> None:
    """清单不存在 = 未配置 MCP（等同不启用，而不是报错）。"""
    assert load_mcp_servers(tmp_path / "nope.json") == []


def test_claude_desktop_format(tmp_path: Path) -> None:
    """兼容 Claude Desktop 的 mcpServers 写法（便于复用生态配置）。"""
    path = _write(
        tmp_path,
        {"mcpServers": {"wellness": {"command": "python", "args": ["s.py"]}}},
    )
    configs = load_mcp_servers(path)
    assert len(configs) == 1
    assert configs[0].name == "wellness"
    assert configs[0].transport == "stdio"
    assert configs[0].args == ["s.py"]


def test_url_implies_http_transport(tmp_path: Path) -> None:
    path = _write(tmp_path, {"mcpServers": {"remote": {"url": "https://example.com/mcp"}}})
    cfg = load_mcp_servers(path)[0]
    assert cfg.transport == "http"
    assert cfg.url == "https://example.com/mcp"


def test_extended_list_format_and_enabled_filter(tmp_path: Path) -> None:
    """扩展写法（servers 列表）且 enabled=false 的项被过滤。"""
    path = _write(
        tmp_path,
        {
            "servers": [
                {"name": "a", "transport": "stdio", "command": "python"},
                {"name": "b", "command": "python", "enabled": False},
            ]
        },
    )
    assert [c.name for c in load_mcp_servers(path)] == ["a"]


def test_placeholders_are_expanded(tmp_path: Path) -> None:
    """${PYTHON} / ${BACKEND_DIR} 展开后跨平台可用（含容器内）。"""
    path = _write(
        tmp_path,
        {
            "mcpServers": {
                "w": {
                    "command": "${PYTHON}",
                    "args": ["${BACKEND_DIR}/mcp_servers/wellness_server.py"],
                }
            }
        },
    )
    cfg = load_mcp_servers(path)[0]
    assert cfg.command == sys.executable
    assert "${" not in cfg.args[0]
    assert cfg.args[0].endswith("wellness_server.py")


def test_expand_leaves_plain_paths_untouched() -> None:
    assert expand_path_placeholders("python") == "python"


def test_validate_flags_missing_command() -> None:
    """stdio 缺 command 时应给出可读错误（启动时只告警、不崩）。"""
    assert "command" in McpServerConfig(name="x").validate()
    assert McpServerConfig(name="x", command="python").validate() == ""
    assert "url" in McpServerConfig(name="y", transport="http").validate()
