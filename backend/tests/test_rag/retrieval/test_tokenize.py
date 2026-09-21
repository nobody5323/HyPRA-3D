"""中文分词测试。"""

import app.rag.retrieval.tokenize as tk


def test_tokenize_chinese_produces_words() -> None:
    """jieba 可用时应切出有意义的词，而不是整句或单字串。"""
    text = "小林最近在准备考试，压力有点大"
    tokens = tk.tokenize(text)

    assert "压力" in tokens
    assert "，" not in tokens          # 标点被过滤
    assert " " not in tokens
    assert len(tokens) < len(text)     # 确实切分了


def test_tokenize_empty_and_blank() -> None:
    assert tk.tokenize("") == []
    assert tk.tokenize("   \n\t ") == []
    assert tk.tokenize("，。！？；") == []


def test_tokenize_lowercases_ascii() -> None:
    tokens = tk.tokenize("Warm Alley 咖啡馆")
    assert "warm" in tokens
    assert "alley" in tokens


def test_tokenizer_name_reports_backend() -> None:
    assert tk.tokenizer_name() in {"jieba", "char-bigram"}


def test_bigram_fallback_when_jieba_missing(monkeypatch) -> None:
    """jieba 不可用时降级为字符 bigram，检索链路不中断。"""
    monkeypatch.setattr(tk, "_jieba", None)
    monkeypatch.setattr(tk, "_jieba_loaded", True)

    tokens = tk.tokenize("失眠")
    assert "失眠" in tokens                    # 双字
    assert "失" in tokens and "眠" in tokens   # 单字
    assert tk.tokenizer_name() == "char-bigram"


def test_tokenize_filters_stopwords() -> None:
    """高频虚词必须被过滤，否则会在 RRF 阶段被当成有效命中。

    回归点：实测 query「ORION-7 交付了吗」时，「了」「吗」几乎命中所有记忆，
    把无关片段抬进了 top 3。
    """
    tokens = tk.tokenize("我的猫是很可爱的吗")
    for stop in ("的", "是", "吗", "我"):
        assert stop not in tokens
    assert "猫" in tokens


def test_stop_words_keep_content_single_chars() -> None:
    """停用词表只剔虚词，单字实词（猫/雨）必须保留。"""
    assert "猫" not in tk.STOP_WORDS
    assert "雨" not in tk.STOP_WORDS
    assert "了" in tk.STOP_WORDS
    assert "吗" in tk.STOP_WORDS


def test_bigram_fallback_handles_ascii(monkeypatch) -> None:
    monkeypatch.setattr(tk, "_jieba", None)
    monkeypatch.setattr(tk, "_jieba_loaded", True)

    tokens = tk.tokenize("Warm Alley 2025")
    assert "warm" in tokens
    assert "alley" in tokens
    assert "2025" in tokens
