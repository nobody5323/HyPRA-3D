"""PyInstaller 打包入口：启动 uvicorn 托管 FastAPI 应用。

为什么需要这个文件：PyInstaller 需要一个「脚本入口」来启动依赖分析，
而项目原先没有——开发与 docker 都用 `uvicorn app.main:app` 命令行形式。

除了正常启动后端，它还承担一个**自举职责**：打包形态下 MCP 的 stdio
子进程同样要用本 exe 启动（见 `run_mcp_server` 的说明）。
"""

import multiprocessing
import os
import sys

import uvicorn

from app.main import app

#: 自举参数：`backend.exe --mcp-server <script.py>`
#: 必须与 app/mcp/config.py 的 MCP_SERVER_FLAG 保持一致
MCP_SERVER_FLAG = "--mcp-server"


def run_mcp_server(script: str) -> None:
    """以 MCP server 模式运行指定脚本（不启动 uvicorn）。

    为什么需要分流：MCP 的 stdio 传输要用一个**可执行文件**拉起 server 进程，
    而打包后 `sys.executable` 是 backend.exe 本身——它不认识 .py 参数，会被
    当成 uvicorn 启动，导致端口冲突与无限自我重启。

    这里用 runpy 在**本进程内**执行那个脚本（脚本的 `__main__` 块照常运行），
    于是 backend.exe 同时充当「Web 服务」与「MCP server 运行时」两种角色，
    不必为此再打一个 exe。
    """
    import runpy

    path = os.path.abspath(script)

    if not os.path.exists(path):
        raise SystemExit(f"MCP server 脚本不存在：{path}")

    # 让被执行的脚本看到干净的 argv（它自己就是「主脚本」）
    sys.argv = [path]
    runpy.run_path(path, run_name="__main__")


def main() -> None:
    argv = sys.argv[1:]

    if argv and argv[0] == MCP_SERVER_FLAG:
        if len(argv) < 2:
            raise SystemExit(f"用法：backend.exe {MCP_SERVER_FLAG} <script.py>")

        run_mcp_server(argv[1])

        return

    # 监听地址可用环境变量覆盖（Electron 主进程按需注入；默认本机回环）
    host = os.environ.get("HYPRA_HOST", "127.0.0.1")
    port = int(os.environ.get("HYPRA_PORT", "8000"))

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=os.environ.get("HYPRA_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    # Windows 下 PyInstaller 产物若产生子进程，缺这行会反复启动自己
    multiprocessing.freeze_support()
    main()
