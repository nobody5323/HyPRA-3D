"""MCP 接入层（HyPRA 作为 MCP **Client**）。

职责：连接外部 MCP 服务器，把它们提供的工具桥接进 HyPRA 的 Agent 行动层
（`app/tools/registry.py` 的 ToolRegistry），使模型能够像调用内置工具一样
调用外部能力「办事」。

模块划分：
    config.py   服务器清单（兼容 Claude Desktop 的 mcpServers 写法）
    session.py  常驻会话：后台事件循环 + 同步门面（解决 asyncio ↔ 同步编排链）
    bridge.py   MCP 工具 → ToolSpec 的注册桥接

设计立场：连接失败**不阻断**应用启动（只记录错误），沿用项目「降级可用」的一贯思路。
"""
