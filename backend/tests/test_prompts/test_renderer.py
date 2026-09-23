"""分层提示词渲染器测试。"""

from app.prompts.persona.loader import load_builtin_presets
from app.prompts.renderer import (
    BACKGROUND_SECTION_TITLE,
    estimate_tokens,
    render_persona_background,
    render_persona_prompt,
    truncate_to_budget,
)


def _therapist():
    return load_builtin_presets()["therapist-elder-sister"]


def test_render_replaces_vars() -> None:
    """渲染应替换状态变量宏。"""
    result = render_persona_prompt(
        _therapist(),
        state_values={"user_name": "小林", "current_mood": "低落"},
    )
    assert "小林" in result.text
    assert "低落" in result.text
    assert "{{" not in result.text  # 所有宏都应被解析
    assert result.warnings == []
    assert result.truncated is False


def test_render_missing_var_warns() -> None:
    """缺省变量应回退默认值并产生告警。"""
    result = render_persona_prompt(_therapist(), state_values={})
    assert "平静" in result.text  # current_mood 默认值
    assert len(result.warnings) >= 1


def test_truncate_to_budget() -> None:
    """超预算时应裁剪至预算内并标记 truncated。"""
    text = "人设正文" * 200  # 600 汉字 ≈ 420 token
    clipped, truncated = truncate_to_budget(text, max_tokens=100)
    assert truncated is True
    assert estimate_tokens(clipped) <= 100
    assert clipped  # 不应被裁空


def test_truncate_keeps_head() -> None:
    """裁剪应从尾部进行，保留开头（身份基调优先）。"""
    text = "【开头身份基调】" + "中间细节" * 300
    clipped, truncated = truncate_to_budget(text, max_tokens=50)
    assert truncated is True
    assert clipped.startswith("【开头身份基调】")


def test_render_with_budget() -> None:
    """render_persona_prompt 支持 token 预算参数。"""
    result = render_persona_prompt(_therapist(), max_tokens=40)
    assert result.estimated_tokens <= 40


# ---------- 背景故事（用户自建角色卡的第二块）----------


def test_render_background_is_empty_by_default() -> None:
    """内置角色没有背景故事 → 空文本（调用方据此跳过拼接）。"""
    assert _therapist().background == ""
    result = render_persona_background(_therapist())
    assert result.text == ""
    assert result.estimated_tokens == 0


def test_render_background_adds_section_title_and_resolves_macros() -> None:
    """背景故事带块标题，且与正文同源解析 {{变量}} 宏。"""
    persona = _therapist().model_copy(
        update={"background": "{{user_name}}小时候住在海边，能听出潮水的脾气。"}
    )

    result = render_persona_background(persona, {"user_name": "小林"})

    assert result.text.startswith(BACKGROUND_SECTION_TITLE)
    assert "小林小时候住在海边，能听出潮水的脾气。" in result.text
    assert "{{" not in result.text
    assert result.estimated_tokens > 0


def test_render_background_keeps_warnings_from_macros() -> None:
    """背景里用了未提供的变量时同样给出告警（与正文一致），不静默吞掉。"""
    persona = _therapist().model_copy(update={"background": "心情是{{current_mood}}。"})
    result = render_persona_background(persona, {"user_name": "小林"})
    assert "平静" in result.text  # 回退到注册表默认值
    assert result.warnings
