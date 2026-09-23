"""jieba 分词器：中文分词的事实标准。

jieba **惰性加载**：首次调用才载入词典（约 0.5–1s），避免拖慢进程启动。
未安装时 `jieba_available()` 为 False，工厂会改用字符 bigram
（见 `bigram_tokenizer.py`）——检索链路不因缺一个可选包而中断。
"""

from __future__ import annotations

from app.rag.retrieval.tokenize.base import Tokenizer, clean_tokens
from app.rag.retrieval.tokenize.bigram_tokenizer import BigramTokenizer

_jieba = None
_jieba_loaded = False


def load_jieba():
    """惰性加载 jieba；未安装则返回 None（缓存结果，只探测一次）。"""
    global _jieba, _jieba_loaded
    if not _jieba_loaded:
        _jieba_loaded = True
        try:
            import jieba  # type: ignore[import-untyped]  # noqa: PLC0415 - 可选依赖
        except ImportError:
            _jieba = None
        else:
            # 关闭 jieba 的初始化日志（否则首次调用会向 stderr 打印加载信息）
            jieba.setLogLevel(60)
            _jieba = jieba
    return _jieba


def jieba_available() -> bool:
    """当前环境是否可用 jieba。"""
    return load_jieba() is not None


class JiebaTokenizer(Tokenizer):
    """基于 jieba 的中文分词。"""

    name = "jieba"

    def tokenize(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []

        jieba = load_jieba()
        if jieba is None:
            # 绕过工厂直接实例化时依赖可能已不在：退回 bigram，而不是返回空列表
            # （返回空会让 BM25 静默失去召回能力，比降精度难查得多）
            return BigramTokenizer().tokenize(text)

        return clean_tokens(jieba.cut(text))
