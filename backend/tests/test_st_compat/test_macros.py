"""ST 宏解析测试（契约文档 §11）。"""

import random
from datetime import datetime

from app.prompts.st_compat import (
    MacroContext,
    parse_st_preset,
    render_macros,
    render_st_preset,
)

# --------------------------------------------------------------------------
# 变量替换类
# --------------------------------------------------------------------------


def test_variable_macros_replaced() -> None:
    """{{char}} / {{user}} 等变量类宏替换为上下文值。"""
    context = MacroContext(
        char_name="苏澄",
        user_name="小林",
        description="人设正文",
        personality="性格",
        scenario="场景",
        persona="对话者",
    )

    resolved, unresolved = render_macros(
        "{{char}} 对 {{user}}：{{description}}/{{personality}}/{{scenario}}/{{persona}}",
        context,
    )

    assert resolved == "苏澄 对 小林：人设正文/性格/场景/对话者"
    assert unresolved == []


def test_time_and_date_use_injected_now() -> None:
    """时间类宏按注入的当前时间格式化。"""
    resolved, _ = render_macros("{{date}} {{time}}", MacroContext(now=datetime(2026, 3, 4, 9, 5)))

    assert resolved == "2026-03-04 09:05"


def test_comment_macro_outputs_nothing() -> None:
    """{{//注释}} 输出为空（所在行的换行保留）。"""
    resolved, unresolved = render_macros("第一行\n{{//这是注释}}\n第二行", MacroContext())

    assert resolved == "第一行\n\n第二行"
    assert unresolved == []


def test_trim_macro_removes_its_line() -> None:
    """{{trim}} 连同所在行的换行一起删除。"""
    resolved, _ = render_macros("第一行\n{{trim}}\n第二行", MacroContext())

    assert resolved == "第一行\n第二行"


# --------------------------------------------------------------------------
# 会话变量类
# --------------------------------------------------------------------------


def test_setvar_then_getvar(make_preset) -> None:
    """setvar 写入会话变量，getvar 读回。"""
    context = MacroContext()

    resolved, _ = render_macros("{{setvar::mood::低落}}{{getvar::mood}}", context)

    assert resolved == "低落"
    assert context.variables["mood"] == "低落"


def test_getvar_missing_returns_empty() -> None:
    """未设置的变量读取为空串（不报错、不泄漏占位符）。"""
    assert render_macros("{{getvar::nope}}", MacroContext())[0] == ""


def test_variables_are_isolated_between_sessions() -> None:
    """不同会话的 MacroContext 互不影响（按会话隔离）。"""
    session_a, session_b = MacroContext(), MacroContext()

    render_macros("{{setvar::x::1}}", session_a)

    assert render_macros("{{getvar::x}}", session_b)[0] == ""
    assert render_macros("{{getvar::x}}", session_a)[0] == "1"


def test_addvar_incvar_decvar() -> None:
    """追加与自增自减。"""
    counter = MacroContext()

    resolved, _ = render_macros("{{setvar::n::5}}{{incvar::n}}-{{decvar::n}}", counter)
    assert resolved == "6-5"

    appender = MacroContext()
    resolved, _ = render_macros("{{addvar::s::甲}}{{addvar::s::乙}}{{getvar::s}}", appender)
    assert resolved == "甲乙"


def test_incvar_on_non_numeric_starts_from_zero() -> None:
    """变量不是数字时按 0 起步（容错，不抛错）。"""
    context = MacroContext(variables={"n": "abc"})

    assert render_macros("{{incvar::n}}", context)[0] == "1"


# --------------------------------------------------------------------------
# 随机类
# --------------------------------------------------------------------------


def test_random_picks_from_options() -> None:
    """{{random::a,b}} 从候选中取值。"""
    resolved, unresolved = render_macros(
        "{{random::甲,乙,丙}}", MacroContext(rng=random.Random(0))
    )

    assert resolved in {"甲", "乙", "丙"}
    assert unresolved == []


def test_random_without_args_returns_float() -> None:
    """无参数的 {{random}} 返回 0~1 浮点（对齐 ST）。"""
    resolved, _ = render_macros("{{random}}", MacroContext(rng=random.Random(0)))

    assert 0.0 <= float(resolved) <= 1.0


def test_pick_is_stable_within_same_seed() -> None:
    """{{pick::}} 在同一 pick_seed 下结果稳定（同一条消息多次渲染一致）。"""
    context = MacroContext(pick_seed="session-1", rng=random.Random(0))

    first, _ = render_macros("{{pick::a,b,c,d,e}}", context)
    second, _ = render_macros("{{pick::a,b,c,d,e}}", context)

    assert first == second
    assert first in {"a", "b", "c", "d", "e"}


def test_pick_varies_by_seed() -> None:
    """不同 pick_seed 允许给出不同结果（只验证仍落在候选集内）。"""
    resolved, _ = render_macros("{{pick::a,b,c,d,e}}", MacroContext(pick_seed="other"))

    assert resolved in {"a", "b", "c", "d", "e"}


def test_roll_expression() -> None:
    """{{roll::NdM+K}} 掷骰落在合法区间。"""
    context = MacroContext(rng=random.Random(3))

    assert render_macros("{{roll::1d1}}", context)[0] == "1"
    assert 5 <= int(render_macros("{{roll::2d6+3}}", context)[0]) <= 15
    assert 1 <= int(render_macros("{{roll::1d20}}", context)[0]) <= 20


def test_roll_with_invalid_expression_returns_text() -> None:
    """非法掷骰表达式原样返回（不抛错）。"""
    assert render_macros("{{roll::abc}}", MacroContext())[0] == "abc"


def test_last_message_macros() -> None:
    """{{lastUserMessage}} / {{lastCharMessage}} / {{lastMessage}} / {{input}}。"""
    context = MacroContext(
        last_user_message="我说过的话",
        last_char_message="她回的话",
        current_input="这次说的",
    )

    resolved, unresolved = render_macros(
        "U={{lastusermessage}} C={{lastcharmessage}} L={{lastmessage}} I={{input}}", context
    )

    assert resolved == "U=我说过的话 C=她回的话 L=这次说的 I=这次说的"
    assert unresolved == []


def test_space_separated_arguments() -> None:
    """酒馆的另一种参数写法：{{roll 1d1}}、{{setvar k v}}（社区预设里很常见）。"""
    context = MacroContext()

    assert render_macros("{{roll 1d1}}", context)[0] == "1"
    assert render_macros("{{setvar mood 有点低落}}{{getvar mood}}", context)[0] == "有点低落"
    assert context.variables["mood"] == "有点低落"


def test_space_separated_incvar() -> None:
    context = MacroContext()

    assert render_macros("{{setvar n 5}}{{incvar n}}", context)[0] == "6"


def test_extended_time_macros() -> None:
    """{{weekday}} / {{isotime}} / {{isodate}}（2026-03-04 是周三）。"""
    context = MacroContext(now=datetime(2026, 3, 4, 9, 5, 7))

    resolved, unresolved = render_macros("{{weekday}} {{isotime}} {{isodate}}", context)

    assert resolved == "周三 09:05:07 2026-03-04"
    assert unresolved == []


def test_text_helper_macros() -> None:
    """{{newline}} 换行；{{noop}} 输出空（常用作占位）。"""
    resolved, unresolved = render_macros("A{{newline}}B{{noop}}C", MacroContext())

    assert resolved == "A\nBC"
    assert unresolved == []


def test_roll_with_large_sides() -> None:
    """预设里常见 {{roll 1d999999}}（空格写法 + 大面数）。"""
    resolved, unresolved = render_macros(
        "{{roll 1d999999}}", MacroContext(rng=random.Random(7))
    )

    assert 1 <= int(resolved) <= 999999
    assert unresolved == []


# --------------------------------------------------------------------------
# 未识别与边界
# --------------------------------------------------------------------------


def test_unknown_macro_preserved_and_reported() -> None:
    """未知宏保留原文并报告（不静默吞掉用户在正文里写的模板文本）。"""
    resolved, unresolved = render_macros("你好 {{unknown_macro}} 世界", MacroContext())

    assert resolved == "你好 {{unknown_macro}} 世界"
    assert unresolved == ["{{unknown_macro}}"]


def test_plain_braces_untouched() -> None:
    """普通花括号（含 `{ a: 1 }`）不受影响。"""
    text = "代码示例：{ a: 1 } 和半括号 {{ 未闭合"

    resolved, unresolved = render_macros(text, MacroContext())

    assert resolved == text
    assert unresolved == []


def test_empty_text_returns_empty() -> None:
    """空文本直接返回。"""
    assert render_macros("", MacroContext()) == ("", [])


# --------------------------------------------------------------------------
# 与渲染器的集成
# --------------------------------------------------------------------------


def test_renderer_resolves_macros_in_preset_content(make_preset, make_context) -> None:
    """预设条目正文里的宏在渲染时替换。"""
    data = make_preset()
    for item in data["prompts"]:
        if item["identifier"] == "main":
            item["content"] = "请以 {{char}} 的身份回应 {{user}}。"

    rendered = render_st_preset(
        parse_st_preset(data),
        make_context(persona_name="苏澄", user_name="小林"),
    )

    assert rendered.messages[0]["content"] == "请以 苏澄 的身份回应 小林。"
    assert rendered.unresolved_macros == []


def test_renderer_resolves_macros_in_examples(make_preset, make_context) -> None:
    """示例对话里的宏同样替换。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset()),
        make_context(
            examples=[("{{user}} 想聊聊", "{{char}} 在听")],
            persona_name="苏澄",
            user_name="小林",
        ),
    )
    contents = [message["content"] for message in rendered.messages]

    assert "小林 想聊聊" in contents
    assert "苏澄 在听" in contents


def test_renderer_reports_unresolved_macros(make_preset, make_context) -> None:
    """未实现的宏在结果里报到（界面可提示）。"""
    data = make_preset()
    for item in data["prompts"]:
        if item["identifier"] == "main":
            item["content"] = "试试 {{bogus_macro}}。"

    rendered = render_st_preset(parse_st_preset(data), make_context())

    assert rendered.unresolved_macros == ["{{bogus_macro}}"]
    assert any("未实现" in warning for warning in rendered.warnings)
    assert "{{bogus_macro}}" in rendered.messages[0]["content"]   # 原文保留


def test_renderer_shares_variables_across_renders(make_preset, make_context) -> None:
    """setvar 写进会话变量后，下一轮渲染能读到（P4 会按会话持有该字典）。"""
    shared: dict[str, str] = {}
    data = make_preset()
    for item in data["prompts"]:
        if item["identifier"] == "main":
            item["content"] = "{{setvar::mood::低落}}"
        if item["identifier"] == "jailbreak":
            item["content"] = "当前心情：{{getvar::mood}}"

    parsed = parse_st_preset(data)
    rendered = render_st_preset(parsed, make_context(macro_variables=shared))

    assert shared["mood"] == "低落"
    assert "当前心情：低落" in rendered.messages[-1]["content"]


def test_history_text_is_not_macro_substituted(make_preset, make_context) -> None:
    """历史消息是已发生的对话原文，不做宏替换（与界面展示保持一致）。"""
    from app.session.context import ChatTurn

    rendered = render_st_preset(
        parse_st_preset(make_preset()),
        make_context(
            history=[ChatTurn("user", "我说过 {{char}} 这个词")],
            persona_name="苏澄",
        ),
    )

    assert "我说过 {{char}} 这个词" in [message["content"] for message in rendered.messages]
