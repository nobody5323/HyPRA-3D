"""模型输出清理测试（兜底：格式标记 / emoji / 元评论）。"""

from app.prompts.sanitize import has_meta_comment, sanitize_reply


def test_strips_bold_and_emphasis() -> None:
    assert sanitize_reply("这是**很重要**的话") == "这是很重要的话"
    assert sanitize_reply("这是__强调__") == "这是强调"


def test_strips_list_marks() -> None:
    text = "- 第一点\n- 第二点\n1. 第三点"
    assert sanitize_reply(text) == "第一点\n第二点\n第三点"


def test_strips_headings_and_dividers() -> None:
    text = "### 标题\n正文\n---\n更多"
    cleaned = sanitize_reply(text)
    assert "#" not in cleaned
    assert "---" not in cleaned
    assert "标题" in cleaned and "正文" in cleaned


def test_strips_emoji() -> None:
    assert sanitize_reply("今天很好🌱呢") == "今天很好呢"
    assert sanitize_reply("🌙 晚安") == "晚安"
    assert sanitize_reply("抱抱你🫂") == "抱抱你"


def test_strips_inline_code_keeps_content() -> None:
    assert sanitize_reply("试试 `4-7-8 呼吸法` 吧") == "试试 4-7-8 呼吸法 吧"


def test_removes_meta_comment_line() -> None:
    """回归：模型把「规则检查 / 自我纠正」写进正文（实测发生）。"""
    text = (
        "我在这儿听着呢。\n"
        "🌱（注：虽然不能加emoji但是语气可以很软） -> actually no emoji allowed per instructions! Corrected below.\n"
        "今晚先陪你坐一会儿。"
    )
    cleaned = sanitize_reply(text)
    assert "emoji" not in cleaned.lower()
    assert "instructions" not in cleaned.lower()
    assert "Corrected" not in cleaned
    assert "虽然不能" not in cleaned
    # 正常语句必须保留
    assert "我在这儿听着呢" in cleaned
    assert "今晚先陪你坐一会儿" in cleaned


def test_removes_chinese_meta_comment() -> None:
    text = "我在听着。\n（已按要求修正：不使用格式标记）\n你接着说。"
    cleaned = sanitize_reply(text)
    assert "已按要求" not in cleaned
    assert "我在听着" in cleaned


def test_keeps_normal_text_intact() -> None:
    """正常口语不得被改动（兜底清理应保守）。"""
    text = "哎……听起来今天挺难的。想不想说说，是哪件事最让你耗神？"
    assert sanitize_reply(text) == text


def test_collapses_extra_blank_lines() -> None:
    assert sanitize_reply("第一段\n\n\n\n第二段") == "第一段\n\n第二段"


def test_empty_input() -> None:
    assert sanitize_reply("") == ""
    assert sanitize_reply(None) is None  # type: ignore[arg-type]


def test_has_meta_comment_helper() -> None:
    assert has_meta_comment("虽然不能加 emoji") is True
    assert has_meta_comment("我在这儿听着呢") is False


# --------------------------------------------------------------------------
# 形态兜底：旁白 / 动作（只认被标点包裹的写法，不做语义判断）
# --------------------------------------------------------------------------


def test_strips_asterisk_action() -> None:
    """星号包裹的动作：角色扮演里最常见的旁白写法（半角与全角星号都算）。"""
    assert sanitize_reply("*她轻轻笑了笑* 今天还好吗？") == "今天还好吗？"
    assert sanitize_reply("我在这儿。＊叹了口气＊") == "我在这儿。"


def test_strips_paren_only_line() -> None:
    """整行括号旁白：整行删掉，而不是留下一个空行。"""
    text = "（她把杯子推过来）\n你先喝点水吧。"
    assert sanitize_reply(text) == "你先喝点水吧。"


def test_strips_short_inline_paren() -> None:
    """行内短括号插注（不含数字 / 字母）当旁白删。"""
    assert sanitize_reply("没事的（笑），慢慢来。") == "没事的，慢慢来。"
    assert sanitize_reply("好（轻轻点头）") == "好"


def test_keeps_paren_with_digits_or_letters() -> None:
    """含数字 / 拉丁字母的括号更像正常补充说明，不能误删。"""
    assert sanitize_reply("试试（4-7-8 呼吸法）吧") == "试试（4-7-8 呼吸法）吧"
    assert sanitize_reply("（PM2.5）有点高") == "（PM2.5）有点高"


def test_emphasis_stripped_before_action() -> None:
    """`**加粗**` 先按强调剥离，不会被误判成星号动作。"""
    assert sanitize_reply("**今天**也辛苦你了") == "今天也辛苦你了"


def test_pure_narration_falls_back_to_original() -> None:
    """整条回复都是旁白时**回退原文**。

    清理成空串在链路上会被当成「模型没反应」（触发额度重试、用户侧看到一片空白），
    比漏掉一句旁白严重得多——所以这里刻意不返回空串。
    """
    assert sanitize_reply("*她笑了笑*") == "*她笑了笑*"
    assert sanitize_reply("（她笑了笑）") == "（她笑了笑）"
