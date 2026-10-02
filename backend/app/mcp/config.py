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

from app.paths import is_frozen, resource_path, resource_root


def default_servers_file() -> Path:
    """默认清单位置：随程序分发的 mcp_servers.json。"""
    return resource_path("mcp_servers.json")


def expand_path_placeholders(value: str) -> str:
    """展开路径占位符（让同一份清单跨平台/跨容器可用）。

    - `${PYTHON}` → 当前解释器（venv 激活与否都正确，Docker 内同样适用）
    - `${BACKEND_DIR}` → 只读资源根的绝对路径
      （开发形态是 backend/；打包形态是解包目录，两者都含 mcp_servers/）
    """
    return (
        value.replace("${PYTHON}", sys.executable)
        .replace("${BACKEND_DIR}", str(resource_root()))
    )


#: 打包入口的自举参数（必须与 backend/run_backend.py 保持一致）
MCP_SERVER_FLAG = "--mcp-server"


def _adjust_args_for_frozen(command: str, args: list[str]) -> list[str]:
    """打包形态下，把「用本 exe 跑 .py 脚本」改成自举调用。

    frozen 时 `${PYTHON}` 展开成 backend.exe，而 stdio 传输是直接
    `spawn(command, args)` —— backend.exe 不认识 .py 路径，会被当成
    uvicorn 启动，造成端口冲突与无限自我重启。

    改成 `backend.exe --mcp-server <script>`，由打包入口（run_backend.py）
    用 runpy 执行该脚本。开发形态（${PYTHON} = python.exe）不受影响。
    """
    if not is_frozen() or command != sys.executable:
        return args

    if not args or not args[0].lower().endswith(".py"):
        return args

    return [MCP_SERVER_FLAG, *args]


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
        args=_adjust_args_for_frozen(
            command,
            [expand_path_placeholders(str(a)) for a in (raw.get("args") or [])],
        ),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        url=url,
        enabled=bool(raw.get("enabled", True)),
    )


def load_mcp_servers(file_path: str | Path | None = None) -> list[McpServerConfig]:
    """读取服务器清单；文件不存在时返回空列表（视为「未配置 MCP」）。"""
    path = Path(file_path) if file_path else default_servers_file()
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
