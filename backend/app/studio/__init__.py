"""创作工坊：用户自建角色卡与世界书条目的存储与接口。

模块划分：
- `store.py`  本地存储门面（内置只读 + 用户目录可写，同构合并）。

设计说明见 `store.py` 的模块文档。
"""

from app.studio.store import (
    DEFAULT_ROOT,
    LIMITS,
    BuiltinReadOnlyError,
    EntrySummary,
    PersonaSummary,
    StudioError,
    StudioStore,
)

__all__ = [
    "DEFAULT_ROOT",
    "LIMITS",
    "BuiltinReadOnlyError",
    "EntrySummary",
    "PersonaSummary",
    "StudioError",
    "StudioStore",
]
