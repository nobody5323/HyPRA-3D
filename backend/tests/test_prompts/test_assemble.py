"""世界书注入编排测试：按档位分组、预算平分、按深度插入。

档位是为外部来源（酒馆）补齐的（`AGENTS.md §8.4`）：外部条目自带
「插在人设前 / 人设后 / 对话历史倒数第 N 条」的位置语义。
"""

from app.prompts.assemble import (
    DepthInjection,
    insert_at_depth,
    place_worldbook,
)
from app.prompts.renderer import estimate_tokens
from app.worldbook.models import WorldBookEntry


def _entry(
    eid: str,
    content: str,
    priority: int = 0,
    *,
    position: str = "after_char",
    depth: int = 4,
    role: str = "system",
) -> WorldBookEntry:
    return WorldBookEntry(
        id=eid,
        title=eid,
        content=content,
        keys=[],
        priority=priority,
        position=position,
        depth=depth,
        role=role,
    )


# =============================================================
# 原有语义（单档位 = 内置 / 自建条目的默认路径）
# =============================================================


def test_no_hits_returns_empty() -> None:
    placement = place_worldbook([], budget=400)
    assert placement.is_empty
    assert placement.after_char == ""
    assert placement.skipped == []


def test_default_position_goes_to_after_char_block() -> None:
    """不声明档位的条目（内置 YAML / 用户自建）仍进「场景补充」块——行为不变。"""
    placement = place_worldbook([_entry("a", "内容 A。")], budget=10_000)

    assert placement.before_char == ""
    assert placement.after_char == "[a]\n内容 A。"
    assert placement.depth == []


def test_injects_all_within_budget() -> None:
    hits = [_entry("a", "内容 A。" * 10), _entry("b", "内容 B。" * 10)]
    placement = place_worldbook(hits, budget=10_000)

    assert placement.skipped == []
    assert placement.after_char.startswith("[a]") or placement.after_char.startswith("[b]")


def test_low_priority_skipped_when_budget_tight() -> None:
    """预算不足时应优先保住高 priority 条目，低者被跳过并记录。"""
    hits = [
        _entry("high", "高优内容。" * 40, priority=20),
        _entry("low", "低优内容。" * 40, priority=1),
    ]
    budget = estimate_tokens("[high]\n" + "高优内容。" * 40)  # 只够装 high
    placement = place_worldbook(hits, budget=budget)

    assert "high" in placement.after_char
    assert "low" not in placement.after_char
    assert [e.id for e in placement.skipped] == ["low"]


def test_reorder_by_priority_desc() -> None:
    """即使传入无序列表，也应先注入高 priority 条目。"""
    hits = [_entry("low", "低。", priority=1), _entry("high", "高。", priority=99)]
    placement = place_worldbook(hits, budget=10_000)

    assert placement.after_char.index("[high]") < placement.after_char.index("[low]")


def test_zero_budget_skips_all() -> None:
    placement = place_worldbook([_entry("a", "内容 A。"), _entry("b", "内容 B。")], budget=0)

    assert placement.is_empty
    assert len(placement.skipped) == 2


# =============================================================
# 档位分组
# =============================================================


def test_positions_are_grouped_into_their_own_blocks() -> None:
    """`before_char` / `after_char` / `at_depth` 各归各的落点。"""
    hits = [
        _entry("before", "人设前的设定。", position="before_char"),
        _entry("after", "人设后的补充。", position="after_char"),
        _entry("deep", "靠近输入才生效。", position="at_depth", depth=2),
    ]

    placement = place_worldbook(hits, budget=10_000)

    assert placement.before_char == "[before]\n人设前的设定。"
    assert placement.after_char == "[after]\n人设后的补充。"
    assert [(i.depth, i.text) for i in placement.depth] == [(2, "[deep]\n靠近输入才生效。")]


def test_positions_without_a_slot_are_folded_with_a_warning() -> None:
    """作者注 / 示例消息档本项目没有对应槽位 → 并入场景补充块，但必须说出来。"""
    hits = [
        _entry("an", "作者注档。", position="before_an"),
        _entry("em", "示例消息档。", position="em_bottom"),
    ]

    placement = place_worldbook(hits, budget=10_000)

    assert "[an]" in placement.after_char
    assert "[em]" in placement.after_char
    assert placement.before_char == ""
    assert placement.depth == []
    assert sorted(placement.warnings) == [
        "1 条「作者注」档的条目没有对应槽位，已并入场景补充块",
        "1 条「示例消息」档的条目没有对应槽位，已并入场景补充块",
    ]


def test_at_depth_keeps_role_and_depth() -> None:
    hits = [_entry("deep", "正文。", position="at_depth", depth=7, role="user")]
    (injection,) = place_worldbook(hits, budget=10_000).depth

    assert (injection.depth, injection.role) == (7, "user")


def test_skipped_entries_keep_injection_order() -> None:
    """跳过的条目按原注入次序回传，便于界面逐条解释「为什么没进去」。"""
    hits = [
        _entry("low", "低。" * 200, priority=1),
        _entry("high", "高。" * 200, priority=99),
    ]
    placement = place_worldbook(hits, budget=1)

    assert [e.id for e in placement.skipped] == ["high", "low"]


# =============================================================
# 预算在档位之间平分
# =============================================================


def test_budget_is_shared_evenly_across_positions() -> None:
    """★ 预算在非空档位间平分。

    先到先得的话，条目多的档位会把别的档位饿死（实测数据 before_char 149 条、
    at_depth 70 条）；平分保证 `at_depth` 这种「靠临近输入施加强影响」的档位
    不会被前一个档位吃光。
    """
    hits = [
        _entry("deep", "深。" * 20, position="at_depth", depth=1),
        _entry("before", "前。" * 200, position="before_char"),
    ]

    placement = place_worldbook(hits, budget=200)

    # 两个档位都对半分到预算：at_depth 的短条目进得去，before_char 的长条被裁
    assert [i.text for i in placement.depth] == ["[deep]\n" + "深。" * 20]
    assert placement.before_char == ""
    assert [e.id for e in placement.skipped] == ["before"]


def test_single_position_gets_the_whole_budget() -> None:
    """只有一个档位时行为与从前完全一致（预算不缩水）。"""
    hits = [_entry("a", "内。" * 60)]
    budget = estimate_tokens("[a]\n" + "内。" * 60)

    placement = place_worldbook(hits, budget=budget)

    assert placement.after_char == "[a]\n" + "内。" * 60


# =============================================================
# 深度插入
# =============================================================


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "上一轮问"},
        {"role": "assistant", "content": "上一轮答"},
        {"role": "user", "content": "这一轮问"},
    ]


def test_depth_zero_goes_after_the_last_message() -> None:
    """depth 0 = 最后一条之后（对齐 ST）。"""
    result = insert_at_depth(_messages(), [DepthInjection(depth=0, text="深")])

    assert [m["content"] for m in result][-1] == "深"


def test_depth_one_goes_before_the_last_message() -> None:
    """depth 1 = 最后一条之前。"""
    result = insert_at_depth(_messages(), [DepthInjection(depth=1, text="深")])

    assert [m["content"] for m in result] == [
        "系统提示",
        "上一轮问",
        "上一轮答",
        "深",
        "这一轮问",
    ]


def test_huge_depth_never_lands_before_the_system_prompt() -> None:
    """★ depth 超出消息条数时钳在开头 system 段之后。

    跑到系统提示前面就等于把系统提示挤到后面，语义完全反了。
    """
    result = insert_at_depth(_messages(), [DepthInjection(depth=999, text="深")])

    assert result[0]["content"] == "系统提示"
    assert result[1]["content"] == "深"


def test_leading_system_run_is_kept_together() -> None:
    """开头连着多条 system（ST 预设常见）时，注入落在最后一条 system 之后。"""
    base = [
        {"role": "system", "content": "第一条"},
        {"role": "system", "content": "第二条"},
        {"role": "user", "content": "问"},
    ]
    result = insert_at_depth(base, [DepthInjection(depth=99, text="深")])

    assert [m["content"] for m in result] == ["第一条", "第二条", "深", "问"]


def test_same_depth_merges_by_role() -> None:
    """同 depth 的注入按 role 合并成一条——同一位置塞多条消息只会把上下文切碎。"""
    injections = [
        DepthInjection(depth=1, text="甲"),
        DepthInjection(depth=1, text="乙"),
        DepthInjection(depth=1, text="丙", role="user"),
    ]
    result = insert_at_depth(_messages(), injections)

    contents = [m["content"] for m in result]
    assert contents == ["系统提示", "上一轮问", "上一轮答", "甲\n\n乙", "丙", "这一轮问"]


def test_no_injections_returns_a_copy() -> None:
    base = _messages()
    result = insert_at_depth(base, [])

    assert result == base
    assert result is not base
