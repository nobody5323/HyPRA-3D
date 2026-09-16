"""MCP 常驻会话：后台事件循环 + 同步门面。

要解决的问题：MCP 官方 SDK 是 **asyncio** 的（`ClientSession` / `stdio_client` 都是
async context manager，且内部用 anyio task group —— 必须在同一个存活的任务里使用），
而 HyPRA 的编排链是**同步**的：

    LangGraph 节点 → ToolRegistry.execute → ToolSpec.handler

因此这里做一层桥接：一个**常驻后台事件循环线程**承载 asyncio 会话，
同步侧通过 `asyncio.run_coroutine_threadsafe(...).result(timeout)` 调用。
好处是无论调用多少次工具，都复用同一条连接（stdio 子进程只启动一次）。

生命周期：`start()` → 可多次 `call_tool_sync()` → `stop()`。
连接失败不抛异常给调用方，只记录到 `error`，由上层决定降级（沿用项目一贯思路）。
"""

from __future__ import annotations

import asyncio
import os
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

# 官方 SDK：绝对导入，与本包（app.mcp）同名但不在同一命名空间，不冲突
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.mcp.config import McpServerConfig


@dataclass
class McpToolInfo:
    """一个 MCP 工具的定义（从服务器 list_tools 得到）。"""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})


def flatten_call_result(result: object) -> str:
    """把 MCP 的 CallToolResult 压成可回传给模型的文本。"""
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    body = "\n".join(parts) if parts else "（外部工具未返回文本内容）"
    if getattr(result, "isError", False):
        return f"外部工具报告错误：{body}"
    return body


class McpSession:
    """一个 MCP 服务器的常驻会话（同步门面）。"""

    def __init__(self, config: McpServerConfig, *, call_timeout: float = 30.0) -> None:
        self.config = config
        self.call_timeout = call_timeout
        self.tools: list[McpToolInfo] = []
        self.error: str = ""

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._session: ClientSession | None = None

    # ---------- 属性 ----------

    @property
    def connected(self) -> bool:
        return self._session is not None and not self.error

    # ---------- 后台事件循环 ----------

    async def _open_session(self) -> tuple[ClientSession, object]:
        """在后台循环内建立连接（返回 session；退出由 AsyncExitStack 负责）。"""
        stack = self._stack
        if self.config.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(self.config.url)
            )
        else:
            params = StdioServerParameters(
                command=self.config.command,
                args=list(self.config.args),
                env={**os.environ, **self.config.env} if self.config.env else None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def _serve(self) -> None:
        """长期运行的协程：建连接 → 等停止信号 → 退出（AsyncExitStack 自动清理）。

        注意：所有 async context 必须在**同一个任务**内进出 —— 这正是这里
        把「连接建立」和「等待停止」放在同一个协程里的原因（anyio task group 约束）。
        """
        self._stop_event = asyncio.Event()
        self._stack = AsyncExitStack()
        try:
            async with self._stack:
                session = await self._open_session()
                self._session = session

                listed = await session.list_tools()
                self.tools = [
                    McpToolInfo(
                        name=t.name,
                        description=(t.description or "").strip(),
                        input_schema=t.inputSchema
                        or {"type": "object", "properties": {}},
                    )
                    for t in listed.tools
                ]
                self._ready.set()
                await self._stop_event.wait()  # 保持会话存活，直到 stop()
        except Exception as exc:  # noqa: BLE001 - 连接失败只记录，不阻断应用
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._session = None
            self._ready.set()  # 确保 start() 不会一直等下去

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        finally:
            loop.close()

    # ---------- 同步门面 ----------

    def start(self, timeout: float = 30.0) -> bool:
        """启动后台线程并等待连接就绪。返回是否成功（失败原因见 self.error）。"""
        err = self.config.validate()
        if err:
            self.error = f"配置不合法：{err}"
            return False

        self._thread = threading.Thread(
            target=self._thread_main, name=f"mcp-{self.config.name}", daemon=True
        )
        self._thread.start()

        if not self._ready.wait(timeout):
            self.error = self.error or f"连接超时（{timeout:.0f}s）"
            return False
        return self.connected

    def stop(self, timeout: float = 10.0) -> None:
        """发送停止信号并等待后台线程退出（幂等）。"""
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return

        def _signal() -> None:
            if self._stop_event is not None:
                self._stop_event.set()

        try:
            loop.call_soon_threadsafe(_signal)
        except RuntimeError:
            return  # 循环已关闭
        thread.join(timeout)

    def call_tool_sync(
        self, name: str, arguments: dict | None = None, *, timeout: float | None = None
    ) -> str:
        """同步调用 MCP 工具，返回文本结果（失败会抛异常，由 ToolRegistry 隔离）。"""
        loop, session = self._loop, self._session
        if loop is None or session is None:
            raise RuntimeError(f"MCP 服务器「{self.config.name}」未连接")

        future = asyncio.run_coroutine_threadsafe(
            session.call_tool(name, arguments or {}), loop
        )
        return flatten_call_result(future.result(timeout or self.call_timeout))
