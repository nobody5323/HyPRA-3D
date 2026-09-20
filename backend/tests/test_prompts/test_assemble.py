"""世界书注入编排测试。"""

from app.prompts.assemble import assemble_worldbook_section
from app.prompts.renderer import estimate_tokens
from app.worldbook.models import WorldBookEntry


def _entry(eid: str, content: str, priority: int = 0) -> WorldBookEntry:
    return WorldBookEntry(id=eid, title=eid, content=content, keys=[], priority=priority)


def test_no_hits_returns_empty() -> None:
    text, skipped = assemble_worldbook_section([], budget=400)
    assert text == ""
    assert skipped == []


def test_injects_all_within_budget() -> None:
    hits = [
        _entry("a", "内容 A。" * 10),
        _entry("b", "内容 B。" * 10),
    ]
    text, skipped = assemble_worldbook_section(hits, budget=10_000)
    assert skipped == []
    assert text.startswith("[a]") or text.startswith("[b]")


def test_low_priority_skipped_when_budget_tight() -> None:
    """预算不足时应优先保住高 priority 条目，低者被跳过并记录。"""
    hits = [
        _entry("high", "高优内容。" * 40, priority=20),
        _entry("low", "低优内容。" * 40, priority=1),
    ]
    budget = estimate_tokens("[high]\n" + "高优内容。" * 40)  # 只够装 high
    text, skipped = assemble_worldbook_section(hits, budget=budget)
    assert "high" in text
    assert "low" not in text
    assert [e.id for e in skipped] == ["low"]


def test_reorder_by_priority_desc() -> None:
    """即使传入无序列表，也应先注入高 priority 条目。"""
    hits = [
        _entry("low", "低。", priority=1),
        _entry("high", "高。", priority=99),
    ]
    text, _ = assemble_worldbook_section(hits, budget=10_000)
    assert text.index("[high]") < text.index("[low]")


def test_zero_budget_skips_all() -> None:
    hits = [_entry("a", "内容 A。"), _entry("b", "内容 B。")]
    text, skipped = assemble_worldbook_section(hits, budget=0)
    assert text == ""
    assert len(skipped) == 2
