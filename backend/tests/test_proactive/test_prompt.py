"""主动开口提示词单测。

两条要点：
1. 合成的内部输入必须**带前缀**——即使行为约束块被预算裁掉，
   模型也还有一次机会看出这不是用户说的话；
2. 「不说」的两种形态（显式标记 / 空回复）都必须被识别——
   空回复漏判会在界面上留下一个**空气泡**。
"""

from app.proactive.models import ProactiveIntent
from app.proactive.prompt import (
    DECLINE_MARKER,
    PROACTIVE_SECTION,
    build_proactive_block,
    build_proactive_input,
    is_declined,
)


def _intent(**overrides) -> ProactiveIntent:
    base = dict(trigger_id="late-night", reason="现在是 02:14，用户还在活动。")
    base.update(overrides)
    return ProactiveIntent(**base)


class TestProactiveInput:
    def test_carries_prefix_and_reason(self) -> None:
        text = build_proactive_input(_intent())
        assert text.startswith("[系统触发·非用户输入]")
        assert "现在是 02:14，用户还在活动。" in text

    def test_includes_facts(self) -> None:
        text = build_proactive_input(_intent(facts=("现在是 02:14",)))
        assert "- 现在是 02:14" in text

    def test_no_facts_section_when_empty(self) -> None:
        assert "你此刻感知到的事实" not in build_proactive_input(_intent())


class TestProactiveBlock:
    def test_has_section_title_and_length_limit(self) -> None:
        block = build_proactive_block(char_limit=60)
        assert PROACTIVE_SECTION in block
        assert "60 字" in block

    def test_declares_this_is_not_a_user_message(self) -> None:
        """不声明的话，模型会把「触发原因：用户凌晨 2 点还在活动」当成用户说的话。"""
        block = build_proactive_block()
        assert "没有用户消息" in block
        assert "[系统触发·非用户输入]" in block

    def test_forbids_interrogation_and_system_tone(self) -> None:
        block = build_proactive_block()
        assert "不要追问" in block
        assert "不像通知" in block

    def test_offers_decline_option(self) -> None:
        """一个**必须**说点什么的陪伴者，迟早会说废话。"""
        assert DECLINE_MARKER in build_proactive_block()

    def test_reason_is_not_repeated_in_block(self) -> None:
        """约束块是行为说明：把触发原因复述一遍等于给模型第二次机会去念它。"""
        block = build_proactive_block(reason="用户正在听《起风了》")
        assert "起风了" not in block

    def test_char_limit_is_floored(self) -> None:
        assert "10 字" in build_proactive_block(char_limit=0)


class TestDecline:
    def test_marker(self) -> None:
        assert is_declined(DECLINE_MARKER) is True

    def test_marker_with_surrounding_space(self) -> None:
        assert is_declined(f"  {DECLINE_MARKER}\n") is True

    def test_empty_reply_counts_as_decline(self) -> None:
        """空回复必须一起算——否则会推一个空气泡到前端。"""
        assert is_declined("") is True
        assert is_declined("   \n ") is True

    def test_normal_reply_is_not_decline(self) -> None:
        assert is_declined("还没睡呀？") is False

    def test_marker_with_trailing_explanation_still_declines(self) -> None:
        assert is_declined(f"{DECLINE_MARKER}（他现在应该不想说话）") is True
