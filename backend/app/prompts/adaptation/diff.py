"""覆盖层补丁的「人类可读差异」：把补丁翻译成界面可直接渲染的前后对照。

为什么单独一层：补丁是给 `apply_override` 看的（键名必须是 ST 字段名与条目
identifier），而界面要展示的是「哪个条目的哪个字段，从什么变成了什么」。
两者的关注点不同，在这里转换一次，避免前端重复实现 ST 字段名的知识。
"""

from __future__ import annotations

from typing import Any

from app.prompts.st_compat import ParsedPreset

#: 字段的界面显示名（未收录的字段直接显示原名，不静默丢弃）
_FIELD_LABELS = {
    "show_thoughts": "推理模式",
    "openai_max_tokens": "回复长度上限",
    "openai_max_context": "提示词总量预算",
    "temperature": "温度",
    "top_p": "top_p",
    "content": "正文",
    "enabled": "启用",
    "role": "角色",
    "prompt_order": "注入顺序",
}


def build_diff(parsed: ParsedPreset, patch: dict) -> list[dict[str, Any]]:
    """把覆盖层补丁翻译成前后对照清单。

    返回项的 `scope` 取值：`preset`（预设级字段）/ `item`（条目字段）/
    `order`（注入顺序）。顺序项额外带 `moved`，描述条目从哪个位置挪到哪个位置。
    """
    if not patch:
        return []

    entries: list[dict[str, Any]] = []

    for field, after in (patch.get("assembly") or {}).items():
        entries.append(
            _entry("preset", "", "", field, _preset_field(parsed, field), after)
        )

    for field, after in (patch.get("sampling") or {}).items():
        entries.append(
            _entry("preset", "", "", field, getattr(parsed.preset, field, None), after)
        )

    current_enabled = {entry.identifier: entry.enabled for entry in parsed.order}
    for identifier, fields in (patch.get("prompts") or {}).items():
        item = parsed.get(identifier)
        if item is None or not isinstance(fields, dict):
            continue
        for field, after in fields.items():
            before = (
                current_enabled.get(identifier)
                if field == "enabled"
                else getattr(item, field, None)
            )
            entries.append(_entry("item", identifier, item.display_name, field, before, after))

    order_entry = _order_entry(parsed, patch.get("prompt_order"))
    if order_entry is not None:
        entries.append(order_entry)

    return entries


def _entry(
    scope: str, identifier: str, name: str, field: str, before: Any, after: Any
) -> dict[str, Any]:
    """构造一条对照项。"""
    return {
        "scope": scope,
        "identifier": identifier,
        "name": name,
        "field": field,
        "field_label": _FIELD_LABELS.get(field, field),
        "before": before,
        "after": after,
    }


def _order_entry(parsed: ParsedPreset, order: Any) -> dict[str, Any] | None:
    """构造注入顺序的对照项（只列**位置发生变化**的条目）。"""
    if not isinstance(order, list) or not order:
        return None

    before_ids = [entry.identifier for entry in parsed.order]
    after_ids = [identifier for identifier in order if isinstance(identifier, str)]
    if not after_ids:
        return None

    moved: list[dict[str, Any]] = []
    for identifier in after_ids:
        if identifier not in before_ids:
            continue
        from_index = before_ids.index(identifier)
        to_index = after_ids.index(identifier)
        if from_index == to_index:
            continue
        item = parsed.get(identifier)
        moved.append(
            {
                "identifier": identifier,
                "name": item.display_name if item is not None else identifier,
                "from_index": from_index,
                "to_index": to_index,
            }
        )

    if not moved:
        return None

    entry = _entry("order", "", "", "prompt_order", None, None)
    entry["moved"] = moved
    return entry


def _preset_field(parsed: ParsedPreset, field: str) -> Any:
    """读取预设字段（声明字段与 extra 字段都能取到）。"""
    value = getattr(parsed.preset, field, None)
    if value is None:
        extra = getattr(parsed.preset, "model_extra", None) or {}
        return extra.get(field)
    return value
