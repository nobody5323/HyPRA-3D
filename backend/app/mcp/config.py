"""MCP 服务器清单的加载与校验。

配置文件默认 `backend/mcp_servers.json`（可用 `MCP_SERVERS_FILE` 覆盖）。
为便于复用生态配置，**兼容 Claude Desktop 的 `mcpServers` 写法**：

    {
      "mcpServers": {
        "wellness": {
          "command": "python",
          "args": ["mcp_servers/wellness_server.py"],
          "env": {"KEY": "value"}
        },
        "remote": { "url": "https://example.com/mcp" }
      }
    }

也支持本项目扩展写法（可显式指定 transport / enabled）：

    {
      "servers": [
        {"name": "wellness", "transport": "stdio", "command": "python",
         "args": ["mcp_servers/wellness_server.py"], "enabled": true}
      ]
    }
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: 默认清单位置：backend/ 目录下（本文件位于 backend/app/mcp/config.py）
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SERVERS_FILE = _BACKEND_DIR / "mcp_servers.json"


def expand_path_placeholders(value: str) -> str:
    """展开路径占位符（让同一份清单跨平台/跨容器可用）。

    - `${PYTHON}` → 当前解释器（venv 激活与否都正确，Docker 内同样适用）
    - `${BACKEND_DIR}` → backend 目录的绝对路径
    """
    return (
        value.replace("${PYTHON}", sys.executable)
        .replace("${BACKEND_DIR}", str(_BACKEND_DIR))
    )


@dataclass
class McpServerConfig:
    """一个 MCP 服务器的连接配置。"""

    name: str
    transport: str = "stdio"  # stdio（本地子进程）| http（Streamable HTTP）
    command: str = ""         # stdio：可执行文件
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""             # http：服务地址
    enabled: bool = True

    @property
    def label(self) -> str:
        """界面/日志用的一行描述。"""
        target = self.url if self.transport == "http" else f"{self.command} {' '.join(self.args)}".strip()
        return f"{self.name}（{self.transport}: {target}）"

    def validate(self) -> str:
        """返回错误说明（空串 = 合法）。"""
        if not self.name:
            return "缺少 name"
        if self.transport not in ("stdio", "http"):
            return f"不支持的 transport：{self.transport}"
        if self.transport == "stdio" and not self.command:
            return "stdio 方式需要 command"
        if self.transport == "http" and not self.url:
            return "http 方式需要 url"
        return ""


def _entry_to_config(name: str, raw: dict) -> McpServerConfig:
    """把一条配置项转成 McpServerConfig（自动识别 transport + 展开路径占位符）。"""
    url = expand_path_placeholders(str(raw.get("url") or "").strip())
    command = expand_path_placeholders(str(raw.get("command") or "").strip())
    transport = str(raw.get("transport") or ("http" if url and not command else "stdio")).strip()
    return McpServerConfig(
        name=name,
        transport=transport,
        command=command,
        args=[expand_path_placeholders(str(a)) for a in (raw.get("args") or [])],
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        url=url,
        enabled=bool(raw.get("enabled", True)),
    )


def load_mcp_servers(file_path: str | Path | None = None) -> list[McpServerConfig]:
    """读取服务器清单；文件不存在时返回空列表（视为「未配置 MCP」）。"""
    path = Path(file_path) if file_path else _DEFAULT_SERVERS_FILE
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"MCP 清单 {path.name} 顶层必须是对象")

    configs: list[McpServerConfig] = []

    # 写法一：Claude Desktop 兼容
    servers = data.get("mcpServers")
    if isinstance(servers, dict):
        for name, raw in servers.items():
            if isinstance(raw, dict):
                configs.append(_entry_to_config(str(name), raw))

    # 写法二：本项目扩展（列表形式，可显式写 name）
    listed = data.get("servers")
    if isinstance(listed, list):
        for raw in listed:
            if isinstance(raw, dict):
                configs.append(_entry_to_config(str(raw.get("name") or ""), raw))

    return [c for c in configs if c.enabled]
