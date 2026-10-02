"""世界书条目加载测试。"""

import pytest

from app.worldbook.loader import load_builtin_entries, load_entry_file


def test_builtin_entries_loaded() -> None:
    """内置条目应加载齐全。"""
    entries = load_builtin_entries()
    ids = {e.id for e in entries}
    assert {"consulting-room", "night-mode", "pet-cat"} <= ids


def test_every_entry_has_trigger() -> None:
    """每条目都应有触发条件（关键词 / 正则 / 向量任一，或常驻）。"""
    for entry in load_builtin_entries():
        assert entry.has_trigger, f"条目 {entry.id} 缺少触发条件"


def test_entry_fields() -> None:
    """字段完整性抽查。"""
    entries = {e.id: e for e in load_builtin_entries()}
    room = entries["consulting-room"]
    assert "咨询室" in room.keys
    assert "梧桐" in room.content  # 自创细节存在
    assert room.priority == 10

    night = entries["night-mode"]
    assert night.keys == []
    assert night.regex  # 正则条目


def test_constant_entry_without_trigger_words_loads(tmp_path) -> None:
    """常驻条目允许没有触发词——否则真实酒馆世界书一半以上条目会被判非法。"""
    path = tmp_path / "constant.yaml"
    path.write_text(
        "id: constant-demo\n"
        "title: 常驻设定\n"
        "content: 每轮都注入的设定\n"
        "constant: true\n"
        "position: at_depth\n"
        "depth: 4\n",
        encoding="utf-8",
    )

    entry = load_entry_file(path)

    assert entry.constant is True
    assert entry.has_trigger is True
    assert (entry.position, entry.depth) == ("at_depth", 4)


def test_entry_without_trigger_and_not_constant_is_rejected(tmp_path) -> None:
    """既非常驻又无触发条件 → 报错，且提示里说清 constant 这条出路。"""
    path = tmp_path / "invalid.yaml"
    path.write_text(
        "id: invalid-demo\ntitle: 缺触发\ncontent: 正文\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="constant"):
        load_entry_file(path)
