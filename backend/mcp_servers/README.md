# MCP 服务器（本项目作为 MCP **Server** 侧提供）

> **与 `backend/app/mcp/` 的区别**（项目里最容易混淆的一处，先看这张表）：

| 目录 | 扮演角色 | 说明 |
| --- | --- | --- |
| `backend/app/mcp/` | **MCP Client** | HyPRA 后端主动连接**外部** MCP server，把对方的工具接进 Agent 行动层（工具名规范化为 `mcp__<服务器>__<工具>`） |
| `backend/mcp_servers/`（本目录） | **MCP Server** | 本项目自己提供、以**独立进程**运行的工具服务。对后端而言它就是"外部服务"，走同一套 Client 链路接入 |

本目录不参与后端包导入，只作为「被拉起的独立进程」存在——所以它不在 `app/` 内。

## 当前服务器

| 文件 | 注册名 | 能力 |
| --- | --- | --- |
| `wellness_server.py` | `wellness` | 情绪 → 环境音建议；压力 → 呼吸节奏建议（只给文字描述，不内置任何受版权保护的音频素材） |

注册与启停配置在 `backend/mcp_servers.json`（兼容 Claude Desktop 的 `mcpServers` 写法，
支持 `${PYTHON}` / `${BACKEND_DIR}` 占位符，跨平台与容器通用）。

## 手动启动（调试用）

```bash
cd backend && python mcp_servers/wellness_server.py
```

正常情况下无需手动启动：后端启动时按 `mcp_servers.json` 自动以 **stdio** 方式拉起，
连接失败不影响后端启动（降级为无外部工具，见 `app/mcp/manager.py`）。

## 新增一个服务器

1. 在本目录新建脚本，用 `FastMCP` 定义工具；
2. 在 `backend/mcp_servers.json` 的 `mcpServers` 里登记（`command` + `args`）；
3. 内容必须**原创或公有领域**（见 `AGENTS.md` §6 合规红线），不得内嵌第三方语料或素材。
