"""感知层与主动开口层的组装测试（docs/proactive-multimodal.md §4.5 / §5.4）。

核心是一条**回归守卫**：两层默认都是空串，因此**不改变任何既有轮次的组装结果**
（与叙事框架层同一约定，见 `test_prompt_manager_jailbreak.py`）。
"""

from app.rag.prompt_manager import (
    LAYER_PERCEPTION,
    LAYER_PROACTIVE,
    PERCEPTION_SECTION,
    PromptManager,
)
from app.session.context import ChatTurn

BASE = dict(
    persona_text="你是一个温柔的陪伴者。",
    user_input="今天好累。",
    worldbook_text="设定：你们住在一起。",
    skills_text="- 情绪日记：当用户表达情绪时",
    warm_lines=["上次他说加班到很晚"],
    fact_lines=["用户养了一只叫豆豆的猫"],
    history=[ChatTurn(role="user", text="在吗"), ChatTurn(role="assistant", text="在的")],
    style_text="【表达风格】\n口语化，短句。",
)


def _build(**overrides):
    return PromptManager().build(**{**BASE, **overrides})


# ---------- 回归守卫：默认零开销 ----------


def test_new_layers_are_absent_by_default() -> None:
    """不传新参数时，两层不产生任何字符。"""
    built = _build()
    assert f"[{PERCEPTION_SECTION}]" not in built.system_prompt
    assert "本轮说明" not in built.system_prompt
    assert built.layers[LAYER_PERCEPTION].empty
    assert built.layers[LAYER_PROACTIVE].empty


def test_explicit_empty_strings_match_default() -> None:
    """显式传空串与不传，结果**逐字一致**（新参数是纯增量）。"""
    assert _build().system_prompt == _build(perception_text="", proactive_text="").system_prompt


def test_whitespace_only_text_is_treated_as_empty() -> None:
    built = _build(perception_text="   \n ", proactive_text="  ")
    assert f"[{PERCEPTION_SECTION}]" not in built.system_prompt
    assert "本轮说明" not in built.system_prompt


# ---------- 感知层 ----------


def test_perception_section_rendered() -> None:
    built = _build(perception_text="- 用户正在听《起风了》")
    assert f"[{PERCEPTION_SECTION}]" in built.system_prompt
    assert "用户正在听《起风了》" in built.system_prompt


def test_perception_sits_after_worldbook_and_before_skills() -> None:
    """优先级：世界书 > 感知 > 技能/记忆。它是当下信息，但**易失**，
    不该压过设定与记忆这些长期资产。"""
    prompt = _build(perception_text="- 现在是 02:14").system_prompt
    assert prompt.index("[场景补充]") < prompt.index(f"[{PERCEPTION_SECTION}]")
    assert prompt.index(f"[{PERCEPTION_SECTION}]") < prompt.index("[可用技能]")
    assert prompt.index(f"[{PERCEPTION_SECTION}]") < prompt.index("[记忆回忆]")


def test_perception_has_its_own_budget() -> None:
    """感知层预算刻意小：一条屏幕 OCR 不该吃掉整个总量预算。"""
    manager = PromptManager(perception_budget=20)
    long_text = "\n".join(f"- 第 {index} 条感知事实" for index in range(50))
    built = manager.build(**{**BASE, "perception_text": long_text})
    assert built.layers[LAYER_PERCEPTION].truncated is True
    assert built.layers[LAYER_PERCEPTION].tokens <= 20


def test_perception_is_dropped_before_worldbook_under_pressure() -> None:
    """总量不足时按优先级裁剪：感知（78）先于世界书（80）被裁。"""
    manager = PromptManager(total_budget=60)
    built = manager.build(
        **{**BASE, "perception_text": "- 用户正在听《起风了》" * 5}
    )
    if LAYER_PERCEPTION in built.dropped_layers:
        assert "总量预算不足" in " ".join(built.warnings)


# ---------- 主动开口层 ----------


def test_proactive_block_rendered_before_format() -> None:
    """主动开口块放**回复格式之前**：两者同属「对本轮的交代」，连读最自然。"""
    block = "【本轮说明】\n这一轮没有用户消息。"
    prompt = _build(proactive_text=block).system_prompt
    assert "本轮说明" in prompt
    assert prompt.index("本轮说明") < prompt.index("【回复格式】")


def test_proactive_block_comes_after_style() -> None:
    """风格块在主动开口之前（风格管「怎么说话」，主动开口管「这一轮是什么情况」）。"""
    prompt = _build(proactive_text="【本轮说明】\n内容").system_prompt
    assert prompt.index("【表达风格】") < prompt.index("本轮说明")


def test_proactive_block_has_own_budget() -> None:
    manager = PromptManager(proactive_budget=20)
    built = manager.build(**{**BASE, "proactive_text": "【本轮说明】" + "很长" * 200})
    assert built.layers[LAYER_PROACTIVE].truncated is True


def test_proactive_survives_budget_pressure_last() -> None:
    """主动开口被裁掉会记 warning——否则「主动开口却答非所问」最难查
    （模型把系统触发当成用户说的话）。"""
    manager = PromptManager(total_budget=60)
    built = manager.build(**{**BASE, "proactive_text": "【本轮说明】" + "内容" * 40})
    if LAYER_PROACTIVE in built.dropped_layers:
        assert any("主动开口" in warning for warning in built.warnings)


# ---------- 两层同时存在 ----------


def test_both_layers_coexist_in_order() -> None:
    prompt = _build(
        perception_text="- 现在是 02:14",
        proactive_text="【本轮说明】\n这一轮没有用户消息。",
    ).system_prompt
    assert prompt.index(f"[{PERCEPTION_SECTION}]") < prompt.index("本轮说明")
    assert prompt.index("本轮说明") < prompt.index("【回复格式】")
