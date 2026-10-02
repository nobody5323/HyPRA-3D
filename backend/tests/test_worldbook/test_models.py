"""世界书条目模型测试：注入档位、次序与触发条件的取值约束。

这些字段是为**外部来源**（酒馆世界书）补齐的（`AGENTS.md §8.4`）：
真实数据里近一半条目是 `constant` + `position=4` / `depth=4` 的「常驻 + 深度注入」型，
不认这几个字段就基本读不到东西。
"""

import pytest

from app.worldbook.models import (
    POSITION_AT_DEPTH,
    POSITION_AFTER_CHAR,
    POSITIONS,
    ROLES,
    WorldBookEntry,
)


def _entry(**overrides) -> WorldBookEntry:
    base = {"id": "demo", "title": "演示条目", "content": "演示内容", "keys": ["猫"]}
    base.update(overrides)
    return WorldBookEntry.model_validate(base)


# ---------- 注入档位 ----------


def test_position_defaults_to_after_char() -> None:
    """默认档位 = 人设后（与既有内置 / 用户条目的行为一致）。"""
    assert _entry().position == POSITION_AFTER_CHAR


@pytest.mark.parametrize("position", sorted(POSITIONS))
def test_every_position_accepted(position: str) -> None:
    """八个档位都要能过校验（对齐 ST 的 `world_info_position` 0–7）。"""
    assert _entry(position=position).position == position


def test_position_is_case_insensitive() -> None:
    """大小写宽容，统一存小写——来源格式的大小写习惯不该变成硬失败。"""
    assert _entry(position="AT_DEPTH").position == POSITION_AT_DEPTH


def test_unknown_position_rejected() -> None:
    """拼错的档位必须早失败，而不是静默当成 after_char。"""
    with pytest.raises(ValueError, match="非法的注入档位"):
        _entry(position="after_char_extended")


def test_depth_is_range_guarded() -> None:
    with pytest.raises(ValueError):
        _entry(depth=-1)


# ---------- 触发条件 ----------


def test_constant_entry_needs_no_trigger() -> None:
    """常驻条目免触发词：酒馆世界里它以「每轮都注入的设定」存在。"""
    entry = _entry(keys=[], constant=True)
    assert entry.has_trigger is True


def test_non_constant_entry_without_trigger_has_none() -> None:
    """非常驻且三通道全空 → 没有触发条件（由加载器拦下）。"""
    assert _entry(keys=[]).has_trigger is False


def test_constant_defaults_to_false() -> None:
    """既有条目（内置 YAML / 用户自建）不受影响：默认不是常驻。"""
    assert _entry().constant is False


# ---------- 次序 ----------


def test_order_and_depth_default_to_source_convention() -> None:
    """order 默认 100、depth 默认 4 —— 都是 ST 世界书的默认值。"""
    entry = _entry()
    assert (entry.order, entry.depth) == (100, 4)


def test_order_is_not_priority() -> None:
    """priority（本项目的「谁更重要」）与 order（来源格式自带的次序）互不干扰。"""
    entry = _entry(priority=7, order=1)
    assert (entry.priority, entry.order) == (7, 1)


# ---------- 注入角色 ----------


def test_role_defaults_to_system() -> None:
    """不声明角色时按 system 注入（内置 / 自建条目的原有行为）。"""
    assert _entry().role == "system"


@pytest.mark.parametrize("role", ["system", "user", "assistant"])
def test_every_role_accepted(role: str) -> None:
    assert _entry(role=role).role == role


def test_role_is_case_insensitive() -> None:
    assert _entry(role="USER").role == "user"


def test_unknown_role_rejected() -> None:
    """拼错的角色必须早失败，否则会当成 system 静默注入。"""
    with pytest.raises(ValueError, match="非法的注入角色"):
        _entry(role="tool")
