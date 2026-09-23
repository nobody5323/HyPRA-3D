"""补丁 → 前后对照的转换测试（界面直接渲染的那份数据）。"""

from app.prompts.adaptation import build_diff, detect


def test_diff_covers_preset_item_and_order(make_parsed) -> None:
    """三类对照都要产出：预设字段 / 条目字段 / 注入顺序。"""
    parsed = make_parsed()
    result = detect(parsed)

    entries = build_diff(parsed, result.auto_patch)

    preset_entry = next(
        entry for entry in entries if entry["field"] == "show_thoughts"
    )
    assert preset_entry["scope"] == "preset"
    assert preset_entry["before"] is True
    assert preset_entry["after"] is False
    assert preset_entry["field_label"] == "推理模式"

    item_entry = next(entry for entry in entries if entry["scope"] == "item")
    assert item_entry["identifier"]
    assert item_entry["name"]

    order_entry = next(entry for entry in entries if entry["scope"] == "order")
    assert order_entry["moved"]
    assert all(
        move["from_index"] != move["to_index"] for move in order_entry["moved"]
    )


def test_diff_reports_item_toggle_with_current_before_value(make_parsed) -> None:
    """条目开关的 before 要取自当前顺序表的真实启用状态。"""
    parsed = make_parsed()

    entries = build_diff(parsed, {"prompts": {"template-rule": {"enabled": False}}})

    assert len(entries) == 1
    entry = entries[0]
    assert entry["scope"] == "item"
    assert entry["identifier"] == "template-rule"
    assert entry["name"] == "输出模板"
    assert entry["field_label"] == "启用"
    assert entry["before"] is True
    assert entry["after"] is False


def test_diff_reports_sampling_field(make_parsed) -> None:
    """采样参数同样要给出前后值（慢问题的核心指标）。"""
    parsed = make_parsed()

    entries = build_diff(parsed, {"sampling": {"openai_max_tokens": 1024}})

    entry = entries[0]
    assert entry["scope"] == "preset"
    assert entry["field"] == "openai_max_tokens"
    assert entry["field_label"] == "回复长度上限"
    assert entry["before"] == 8192
    assert entry["after"] == 1024


def test_diff_empty_for_empty_patch_or_unchanged_order(make_parsed) -> None:
    """空补丁、以及顺序没真变时不产出噪声对照。"""
    parsed = make_parsed()

    assert build_diff(parsed, {}) == []
    # 顺序表原样回传：没有任何条目换位 → 不该出现「注入顺序」项
    same_order = [entry.identifier for entry in parsed.order]
    assert all(
        entry["scope"] != "order"
        for entry in build_diff(parsed, {"prompt_order": same_order})
    )


def test_diff_skips_unknown_identifier(make_parsed) -> None:
    """补丁引用了不存在的条目时跳过（不崩、也不产出误导性对照）。"""
    parsed = make_parsed()

    assert build_diff(parsed, {"prompts": {"不存在": {"content": "（测试语料）x"}}}) == []


def test_diff_uses_extra_field_for_thinking(make_parsed) -> None:
    """`show_thoughts` 存在 pydantic 的 extra 里，取 before 值要能取到（否则恒为 None）。"""
    parsed = make_parsed()

    entry = build_diff(parsed, {"assembly": {"show_thoughts": False}})[0]

    assert entry["before"] is True
