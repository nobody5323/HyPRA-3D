"""运行时路径解析：开发（源码树）与打包（PyInstaller onedir）的统一入口。

为什么需要这一层：打包后 `__file__` 指向 PyInstaller 的临时解压目录，
进程的 cwd 也不再是 `backend/` —— 项目原先那两条路径来源（模块级 `__file__`
推导、以及「相对 backend 运行目录」的配置字符串）**同时失效**。

三种基准目录的语义完全不同，不可混用：

- `resource_root()`：**只读资源**，随程序分发。提示词预设 / 世界书条目 /
  技能 / 第一方插件 / MCP 清单都在这里；打包后位于 `sys._MEIPASS`。
- `data_root()`：**可写数据**。数据库 / 用户内容 / 插件状态 / 媒体产物；
  打包后位于 `%APPDATA%/HyPRA`。
- `config_file()`：**用户配置** `.env`；打包后位于 `data_root()` 下。

开发形态下后两者都指向 `backend/`，与改造前「相对 backend 运行目录」的行为
保持一致（前提是 uvicorn 的 cwd 就是 `backend/`，启动脚本与 Electron 都是这么做的）。

所有函数都在**调用时**求值、不做缓存，因此进程内改环境变量后重新构造
`Settings()` 即可生效。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: 源码树里的 backend/ 目录（本文件位于 backend/app/paths.py）
_BACKEND_DIR = Path(__file__).resolve().parent.parent

#: 打包形态下的数据目录名。
#: 必须与 Electron 主进程的 `app.setName("HyPRA")` 一致 —— 那样
#: `app.getPath("userData")` 才会落在同一个目录上，两边无需互相传参。
_APP_DIR_NAME = "HyPRA"

#: 数据目录的环境变量覆盖名（供测试与特殊部署使用）
DATA_DIR_ENV = "HYPRA_DATA_DIR"

#: 配置文件的环境变量覆盖名
CONFIG_FILE_ENV = "HYPRA_ENV_FILE"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包产物里。"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根。

    打包后由 PyInstaller 把 `--add-data` 的内容解到 `sys._MEIPASS`；
    开发形态就是 `backend/`。两者都按「相对源码树的路径」拼接
    （如 `resource_path("app", "worldbook", "entries")`），所以同一份
    代码在两种形态下写法一致。
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return _BACKEND_DIR


def data_root() -> Path:
    """可写数据根。

    打包后取 `%APPDATA%/HyPRA`：刻意与 Electron 的
    `app.getPath("userData")` 保持同一位置（主进程已 `app.setName("HyPRA")`），
    这样两边自动指向同一目录，不需要额外注入环境变量。

    开发形态就是 `backend/`（数据本就落在 `backend/data`、`backend/media`）。
    """
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    if is_frozen():
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / _APP_DIR_NAME
    return _BACKEND_DIR


def config_file() -> Path:
    """用户配置 `.env` 的位置。"""
    override = os.environ.get(CONFIG_FILE_ENV)
    if override:
        return Path(override)
    if is_frozen():
        return data_root() / ".env"
    return _BACKEND_DIR / ".env"


def resolve_under(root: Path, value: str) -> str:
    """把配置里的路径值解析成绝对路径。

    绝对路径原样返回（尊重用户在 .env 里手填的位置），
    相对路径挂到 `root` 下。返回字符串是为了直接写回字符串类型的配置字段。
    """
    path = Path(value)
    return str(path if path.is_absolute() else root / path)


def data_path(*parts: str) -> Path:
    """可写数据根下的路径（等价于 `data_root().joinpath(*parts)`）。"""
    return data_root().joinpath(*parts)


def resource_path(*parts: str) -> Path:
    """只读资源根下的路径。"""
    return resource_root().joinpath(*parts)
