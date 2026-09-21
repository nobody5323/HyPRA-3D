"""文档清洗去噪测试。"""

from app.memory.knowledge.cleaner import clean


def test_removes_invisible_and_control_chars() -> None:
    """零宽字符、BOM、软连字符、控制字符都应被移除。"""
    result = clean("小\u200b林\x07喜欢\ufeff下雨天\u00ad")

    assert result.text == "小林喜欢下雨天"
    assert result.hits["invisible_char"] == 3      # \u200b \ufeff \u00ad
    assert result.hits["control_char"] == 1        # \x07


def test_strips_html_tags_and_decodes_entities() -> None:
    result = clean("<p>小林&nbsp;喜欢 <b>下雨天</b></p>")

    assert "<p>" not in result.text
    assert "<b>" not in result.text
    assert "\u00a0" in result.text                 # &nbsp; 解码为不换行空格
    assert "小林" in result.text
    assert result.hits["html_tag"] >= 2


def test_drops_page_number_lines() -> None:
    """PDF 常见的独立页码行应被删除。"""
    text = "正文第一行\n12\n正文第二行\n- 13 -\n第 14 页\n正文第三行"
    result = clean(text)

    assert result.text == "正文第一行\n正文第二行\n正文第三行"
    assert result.hits["page_number"] == 3


def test_drops_repeated_header_lines() -> None:
    """重复出现的短行是页眉/页脚残留，应删除。"""
    text = ""
    for i in range(4):
        text += (
            "内部资料 请勿外传\n"
            f"这是第 {i} 段的正文内容，足够长以避免被当作重复短行。\n"
        )
    result = clean(text)

    assert "内部资料" not in result.text
    assert "这是第 0 段" in result.text
    assert result.hits["repeated_line"] == 4


def test_keeps_markdown_headings_and_rules() -> None:
    """关键：Markdown 标题是分块的一级语义边界，绝不能被当成页眉删掉。"""
    text = "\n".join(["# 第一章", "正文 A", "# 第一章", "正文 B", "# 第一章", "正文 C", "---", "---", "---"])
    result = clean(text)

    assert result.text.count("# 第一章") == 3
    assert result.text.count("---") == 3


def test_joins_hyphenated_line_breaks() -> None:
    """英文 PDF 的断词连字符应拼回。"""
    result = clean("inter-\nnational cooperation")

    assert result.text == "international cooperation"
    assert result.hits["hyphen_break"] == 1


def test_normalizes_whitespace() -> None:
    result = clean("小林   喜欢\t\t下雨天  \n\n\n\n第二段")

    assert result.text == "小林 喜欢 下雨天\n\n第二段"
    assert result.hits["multi_blank"] == 1


def test_keeps_paragraph_structure() -> None:
    """段落结构（空行）必须保留——分块依赖它。"""
    result = clean("第一段。\n\n第二段。")
    assert result.text == "第一段。\n\n第二段。"


def test_stats_fields() -> None:
    result = clean("小林\u200b喜欢下雨天")

    assert result.original_length == 8
    assert result.cleaned_length == 7
    assert 0 < result.removed_ratio < 1


def test_empty_input() -> None:
    result = clean("")

    assert result.text == ""
    assert result.removed_ratio == 0.0
    assert result.hits == {}
