"""分词器接口与共用工具（BM25 稀疏检索通道的入口抽象）。

拆分自原 `tokenize.py`（`AGENTS.md §9.11` P2）：把「中文分词」从一组模块级函数
变成**可替换的策略**——jieba 与字符 bigram 是同一接口的两个实现，
由 `tokenizer_backend` 选择（默认 auto：有 jieba 就用，没有就降级）。

**降级不是妥协而是要求**：真正的语言能力需要外部依赖，但检索机制本身必须始终可用——
CI 与离线演示不因缺一个可选包而报错。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable

#: 提取「中文连续段 / 英文单词 / 数字」，天然丢弃标点与空白
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
WORD = re.compile(r"[A-Za-z]+|\d+")

#: 中文高频虚词（单字）。它们几乎出现在所有句子里，会严重污染 BM25 打分：
#: IDF 虽低，但 RRF 只看**排名**，只要进了候选就会蹭上得分（实测「了」「吗」
#: 会把无关记忆抬进 top 3）。
#: 与 DeterministicEmbeddingProvider 的字符级过滤表保持一致（两处表含义相同：
#: 都是「无区分度的常用字」，但作用粒度不同——此处过滤 token，那里过滤字符）。
STOP_WORDS: frozenset[str] = frozenset(
    "的了是很在我有和就不人都一这中上个也还那要会没她他它吗吧啊哦呀呢么与你"
)


def has_content(token: str) -> bool:
    """token 是否含实义字符（汉字 / 字母 / 数字），用于过滤纯标点。"""
    return any(ch.isalnum() for ch in token)


def clean_tokens(tokens: Iterable[str]) -> list[str]:
    """统一后处理：小写、去空白、去纯标点、去停用词。

    两个实现都走这里，保证过滤规则**只有一份**——否则 jieba 通路与 bigram 通路的
    停用词表会慢慢分叉，表现出来就是「换了分词器以后召回结果莫名变化」。
    """
    return [
        token
        for token in (raw.strip().lower() for raw in tokens)
        if token and has_content(token) and token not in STOP_WORDS
    ]


class Tokenizer(ABC):
    """分词器接口（一种切分策略一个实现）。"""

    #: 实现名（与配置值一致，供调试与健康检查展示）
    name: str = ""

    @abstractmethod
    def tokenize(self, text: str) -> list[str]:
        """把文本切成 token 序列（小写、去标点、去停用词）。"""
