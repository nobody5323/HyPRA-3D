"""分词器工厂：按配置或环境选择实现。

`tokenizer_backend`（见 `app/config.py`）：

- `auto`（默认）—— jieba 可用就用 jieba，否则降级 char-bigram；
- `jieba`       —— **强制** jieba；未安装时抛错。部署问题应在首次检索时明确暴露，
                    而不是悄悄降级成降精度模式（那会让召回质量莫名其妙变差）；
- `char-bigram` —— 强制字符 bigram（零依赖，CI / 离线演示）。

**不缓存实例**：两种实现都无状态，构造开销可忽略；换来的是 jieba 装/卸后无需重启，
以及测试能可靠地模拟「jieba 不可用」——缓存实例会让这两件事都变成坑。
"""

from __future__ import annotations

from app.rag.retrieval.tokenize.base import Tokenizer
from app.rag.retrieval.tokenize.bigram_tokenizer import BigramTokenizer
from app.rag.retrieval.tokenize.jieba_tokenizer import JiebaTokenizer, jieba_available

AUTO = "auto"
JIEBA = "jieba"
CHAR_BIGRAM = "char-bigram"

#: 可选值（供报错信息与文档引用）
BACKENDS: tuple[str, ...] = (AUTO, JIEBA, CHAR_BIGRAM)

#: bigram 的常见别名（配置是手写的，别让人因为拼法不同就启动失败）
_BIGRAM_ALIASES = frozenset({"char-bigram", "char_bigram", "bigram", "char"})


def create_tokenizer(backend: str = AUTO) -> Tokenizer:
    """按名字创建分词器。

    抛出:
        ValueError: 后端名未知，或强制 jieba 但依赖未安装。
    """
    name = (backend or AUTO).strip().lower()
    if name in {"", AUTO}:
        return JiebaTokenizer() if jieba_available() else BigramTokenizer()
    if name == JIEBA:
        if not jieba_available():
            raise ValueError(
                "分词器配置为 jieba，但未安装该依赖："
                "请 pip install jieba，或把 TOKENIZER_BACKEND 改为 char-bigram"
            )
        return JiebaTokenizer()
    if name in _BIGRAM_ALIASES:
        return BigramTokenizer()
    raise ValueError(f"未知分词器后端：{backend!r}（可选 {' | '.join(BACKENDS)}）")


def default_tokenizer() -> Tokenizer:
    """按当前配置取分词器（读 `settings.tokenizer_backend`）。"""
    from app.config import get_settings

    return create_tokenizer(get_settings().tokenizer_backend)


def tokenizer_name() -> str:
    """当前生效的分词器名称（供调试与健康检查）。"""
    return default_tokenizer().name


def tokenize(text: str) -> list[str]:
    """按当前配置分词（检索链路的便捷入口）。"""
    return default_tokenizer().tokenize(text)
