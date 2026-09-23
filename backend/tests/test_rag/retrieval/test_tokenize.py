"""中文分词测试（`app/rag/retrieval/tokenize/`）。

拆包后（§9.11 P2）测试分两类：
- **行为回归**：与拆包前一致的分词结果（标点过滤、停用词、大小写、bigram 降级）；
- **策略装配**：工厂按 `tokenizer_backend` 选实现——这是拆分带来的新能力。

注意：模拟「jieba 不可用」要 patch **`jieba_tokenizer` 模块**的私有变量，
而不是包名（`app.rag.retrieval.tokenize` 是兼容层，本身不持有 jieba 句柄）。
"""

import pytest

import app.rag.retrieval.tokenize as tk
from app.rag.retrieval.tokenize import jieba_tokenizer


def _disable_jieba(monkeypatch) -> None:
    """模拟「未安装 jieba」：把惰性加载的缓存直接置为已探测且为空。"""
    monkeypatch.setattr(jieba_tokenizer, "_jieba", None)
    monkeypatch.setattr(jieba_tokenizer, "_jieba_loaded", True)


# =============================================================
# 行为回归（拆分前后应完全一致）
# =============================================================


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
    _disable_jieba(monkeypatch)

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
    _disable_jieba(monkeypatch)

    tokens = tk.tokenize("Warm Alley 2025")
    assert "warm" in tokens
    assert "alley" in tokens
    assert "2025" in tokens


# =============================================================
# 策略装配（拆分新增：分词器可替换）
# =============================================================


def test_create_tokenizer_explicit_backends() -> None:
    assert tk.create_tokenizer("jieba").name == "jieba"
    assert tk.create_tokenizer("char-bigram").name == "char-bigram"
    # 别名容忍：配置是手写的，不该因为拼法不同就启动失败
    assert tk.create_tokenizer("bigram").name == "char-bigram"
    assert tk.create_tokenizer("CHAR_BIGRAM").name == "char-bigram"


def test_create_tokenizer_auto_follows_environment() -> None:
    """auto 跟随环境：装了 jieba 就用，否则降级。"""
    expected = "jieba" if tk.jieba_available() else "char-bigram"
    assert tk.create_tokenizer("auto").name == expected
    assert tk.create_tokenizer("").name == expected


def test_create_tokenizer_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="未知分词器后端"):
        tk.create_tokenizer("bert")


def test_forced_jieba_without_dependency_raises(monkeypatch) -> None:
    """强制 jieba 但依赖缺失 → 报错，而不是悄悄降级。

    静默降级会让召回质量莫名变差，比启动时明确报错难查得多。
    """
    _disable_jieba(monkeypatch)

    with pytest.raises(ValueError, match="未安装该依赖"):
        tk.create_tokenizer("jieba")
    # 但 auto / bigram 通路仍然可用
    assert tk.create_tokenizer("auto").name == "char-bigram"


def test_jieba_tokenizer_degrades_when_invoked_directly(monkeypatch) -> None:
    """绕过工厂直接实例化 JiebaTokenizer 时也要降级，而不是返回空。

    返回空列表会让 BM25 静默失去召回能力——那种故障比降精度难查得多。
    """
    _disable_jieba(monkeypatch)

    tokens = tk.JiebaTokenizer().tokenize("失眠")
    assert "失眠" in tokens


def test_both_backends_share_stopword_filter() -> None:
    """两个实现共用同一份过滤规则，否则换分词器会带来莫名的召回变化。"""
    text = "我的猫是很可爱的吗"
    for tokenizer in (tk.BigramTokenizer(), tk.create_tokenizer("auto")):
        tokens = tokenizer.tokenize(text)
        assert "的" not in tokens, tokenizer.name
        assert "是" not in tokens, tokenizer.name
        assert "猫" in tokens, tokenizer.name


def test_tokenizer_backend_from_settings(monkeypatch) -> None:
    """`default_tokenizer()` 读配置——这是用户/评审切换分词器的唯一入口。

    用环境变量而不是 patch 示例：`get_settings()` 每次返回新实例（非单例缓存），
    patch 某个实例不会影响后续调用。
    """
    from app.config import get_settings

    monkeypatch.setenv("TOKENIZER_BACKEND", "char-bigram")
    assert get_settings().tokenizer_backend == "char-bigram"
    assert tk.default_tokenizer().name == "char-bigram"
