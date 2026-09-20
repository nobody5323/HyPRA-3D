"""世界书触发匹配器测试。"""

from app.worldbook.matcher import match_entries
from app.worldbook.models import WorldBookEntry


def _entry(**overrides) -> WorldBookEntry:
    base = {
        "id": "demo",
        "title": "演示条目",
        "content": "演示内容",
        "keys": ["猫", "咖啡馆"],
    }
    base.update(overrides)
    return WorldBookEntry.model_validate(base)


def test_keyword_hit() -> None:
    """关键词任一命中即触发。"""
    entry = _entry()
    assert match_entries([entry], "今天路过一家咖啡馆") == [entry]


def test_keyword_miss() -> None:
    """无关键词命中时不触发。"""
    entry = _entry()
    assert match_entries([entry], "今天天气不错") == []


def test_case_insensitive_by_default() -> None:
    """默认不区分大小写。"""
    entry = _entry(keys=["warm alley"])
    assert match_entries([entry], "我在 Warm Alley 等你") == [entry]
    assert match_entries([entry], "我在 WARM ALLEY 等你") == [entry]


def test_case_sensitive() -> None:
    """声明 case_sensitive 后区分大小写。"""
    entry = _entry(keys=["Warm"], case_sensitive=True)
    assert match_entries([entry], "遇见 Warm 咖啡") == [entry]
    assert match_entries([entry], "遇见 warm 咖啡") == []


def test_regex_hit() -> None:
    """正则通道命中。"""
    entry = _entry(keys=[], regex=[r"深夜|失眠"])
    assert match_entries([entry], "我又失眠了") == [entry]
    assert match_entries([entry], "今天天气不错") == []


def test_disabled_entry_skipped() -> None:
    """enabled=False 的条目默认不参与匹配。"""
    entry = _entry(enabled=False)
    assert match_entries([entry], "今天路过一家咖啡馆") == []
    # 显式 include_disabled 后仍可命中（便于调试）
    assert match_entries([entry], "今天路过一家咖啡馆", include_disabled=True) == [entry]


def test_multiple_hits_sorted_by_priority() -> None:
    """多条目命中时按 priority 降序。"""
    low = _entry(id="low", keys=["猫"], priority=1)
    high = _entry(id="high", keys=["猫"], priority=10)
    hits = match_entries([low, high], "我家猫很黏人")
    assert [h.id for h in hits] == ["high", "low"]


# ---------- 向量通道（matcher 只依赖索引的 match() 方法）----------


class _FakeIndex:
    """恒返回固定命中集的假索引，用于隔离验证 matcher 的合并逻辑。"""

    def __init__(self, hits: set[str]) -> None:
        self._hits = hits

    def match(self, text: str) -> set[str]:  # noqa: ARG002 - 接口约定签名
        return set(self._hits)


def test_vector_hit_triggers_entry() -> None:
    """只有向量通道命中的条目也应算命中（无需关键词/正则）。"""
    entry = _entry(id="vec", keys=[], regex=[], vector_text="语义文本")
    hits = match_entries([entry], "完全无关的字面文本", vector_index=_FakeIndex({"vec"}))
    assert hits == [entry]


def test_vector_index_absent_keeps_two_channel_behavior() -> None:
    """未传索引时向量条目不命中（行为与加向量通道前一致）。"""
    entry = _entry(id="vec", keys=[], regex=[], vector_text="语义文本")
    assert match_entries([entry], "完全无关的字面文本") == []


def test_vector_and_keyword_hits_merge_without_duplicates() -> None:
    """两通道同时命中同一条目时只出现一次。"""
    entry = _entry(id="both", keys=["咖啡馆"], vector_text="语义文本")
    hits = match_entries([entry], "路过咖啡馆", vector_index=_FakeIndex({"both"}))
    assert hits == [entry]


def test_vector_hit_respects_disabled() -> None:
    """停用条目即使向量命中也不参与匹配。"""
    entry = _entry(id="vec", keys=[], regex=[], vector_text="语义文本", enabled=False)
    assert match_entries([entry], "随便说点什么", vector_index=_FakeIndex({"vec"})) == []
