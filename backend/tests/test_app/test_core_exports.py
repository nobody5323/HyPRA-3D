"""core 边界清单的可执行断言（`AGENTS.md §9.10` / §9.11 P8）。

`app/core/__init__.py` 只是聚合导出，**它的价值全在于被验证**：
没人检查的话，那份清单会在重构中慢慢与实际实现脱节，
最后变成一段读起来像真的、实际是错的注释——比没有更糟。
"""

from __future__ import annotations

import types

import app.core

#: §9.10 列出的 9 项 core。
#:
#: **刻意硬编码**：清单改名或增减时必须到这里同步一次，
#: 而不是让测试自动跟随实现（那样就失去"边界"的意义了）。
EXPECTED_CAPABILITIES = [
    "prompt-assembly",
    "memory-facade",
    "conversation-graph",
    "session-window",
    "worldbook-matcher",
    "emotion-pipeline",
    "embodiment-timeline",
    "retrieval-fusion",
    "api-skeleton",
]


def test_nine_core_capabilities_are_declared() -> None:
    """恰好 9 项，顺序也与 §9.10 一致（多一项少一项都要有人过问）。"""
    assert list(app.core.CORE_CAPABILITIES) == EXPECTED_CAPABILITIES


def test_every_declared_symbol_exists() -> None:
    """每个声明的符号都要真的取得到——改名或挪走会立刻暴露。"""
    missing: list[str] = []
    for capability, symbols in app.core.CORE_CAPABILITIES.items():
        assert symbols, f"{capability} 没有声明任何公开符号"
        for name in symbols:
            if getattr(app.core, name, None) is None:
                missing.append(f"{capability}.{name}")

    assert missing == []


def test_dunder_all_matches_declarations() -> None:
    """`__all__` 与能力表必须一致，否则 `from app.core import *` 会漏东西。"""
    declared = {name for symbols in app.core.CORE_CAPABILITIES.values() for name in symbols}

    assert set(app.core.__all__) - {"CORE_CAPABILITIES"} == declared


def test_dunder_all_has_no_duplicates() -> None:
    assert len(app.core.__all__) == len(set(app.core.__all__))


def test_dunder_all_is_sorted() -> None:
    """排序不为功能，只为人工比对与 diff 稳定。"""
    assert app.core.__all__ == sorted(app.core.__all__)


def test_exports_are_symbols_not_modules() -> None:
    """导出的是符号而不是模块——拿到模块说明写成了 `from app.x import y` 的形式错误。"""
    for name in app.core.__all__:
        if name == "CORE_CAPABILITIES":
            continue
        assert not isinstance(getattr(app.core, name), types.ModuleType), name


def test_capability_table_is_plain_data() -> None:
    """表是纯数据（能力名 → 符号名元组），不掺逻辑也不夹带说明文字。"""
    for capability, symbols in app.core.CORE_CAPABILITIES.items():
        assert capability and capability.isascii(), capability
        assert isinstance(symbols, tuple), capability
        assert all(isinstance(name, str) and name for name in symbols), capability


def test_core_modules_do_not_import_app_core() -> None:
    """**反向依赖检查**：`app.core` 只是清单，不该被任何实现模块 import。

    一旦某个 core 模块 `from app.core import ...`，清单就从"描述"变成了"依赖"，
    移动文件这件事会从「随时可做」变成「必须先解开这张网」。
    """
    from pathlib import Path

    app_root = Path(app.core.__file__).parents[1]
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        if path.parts[-2:] == ("core", "__init__.py"):
            continue  # 清单自身
        text = path.read_text(encoding="utf-8")
        if "from app.core import" in text or "import app.core" in text:
            offenders.append(str(path.relative_to(app_root)))

    assert offenders == []
