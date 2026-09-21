"""中文分词（BM25 稀疏检索通道的入口）。

优先使用 **jieba**（中文分词的事实标准）；不可用时降级为**字符 bigram**，
保证零依赖环境下检索链路仍可运行（精度下降但不中断）。

降级思路与 `DeterministicEmbeddingProvider` 一致：真正的语言能力需要外部依赖，
但机制本身必须始终可用——CI 与离线演示不因缺一个可选包而报错。

jieba 惰性加载：首次调用才载入词典（约 0.5–1s），避免拖慢进程启动。
"""

from __future__ import annotations

import re

# 提取「中文连续段 / 英文单词 / 数字」，天然丢弃标点与空白
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z]+|\d+")

#: 中文高频虚词（单字）。它们几乎出现在所有句子里，会严重污染 BM25 打分：
#: IDF 虽低，但 RRF 只看**排名**，只要进了候选就会霋得分（实测「了」「吗」
#: 会把无关记忆抬进 top 3）。
#: 与 DeterministicEmbeddingProvider 的字符级过滤表保持一致（两处表含义相同：
#: 都是「无区分度的常用字」，但作用粒度不同——此处过滤 token，那里过滤字符）。
STOP_WORDS: frozenset[str] = frozenset(
    "的了是很在我有和就不人都一这中上个也还那要会没她他它吗吧啊哦呀呢么与你"
)

_jieba = None
_jieba_loaded = False


def _load_jieba():
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


def tokenizer_name() -> str:
    """当前生效的分词器名称（供调试与健康检查）。"""
    return "jieba" if _load_jieba() is not None else "char-bigram"


def _has_content(token: str) -> bool:
    """token 是否含实义字符（汉字 / 字母 / 数字），用于过滤纯标点。"""
    return any(ch.isalnum() for ch in token)


def _bigram_fallback(text: str) -> list[str]:
    """无 jieba 时的降级分词：中文取单字 + 双字，英文/数字整体小写。"""
    tokens: list[str] = list(_WORD.findall(text.lower()))
    for run in _CJK_RUN.findall(text):
        tokens.extend(ch for ch in run if ch not in STOP_WORDS)        # 单字
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))    # 双字
    return tokens


def tokenize(text: str) -> list[str]:
    """把文本切成 token 序列（小写、去标点、**去停用词**）。

    BM25 的召回质量高度依赖分词：中文没有空格，需要 jieba 才能切出
    「加班」「压力」这类有意义的词；同时必须过滤「的/了/吗」这类无区分度的
    虚词，否则它们会在 RRF 融合阶段被当成有效命中。
    """
    if not text or not text.strip():
        return []

    jieba = _load_jieba()
    if jieba is None:
        return _bigram_fallback(text)

    tokens = [token.strip().lower() for token in jieba.cut(text)]
    return [
        token
        for token in tokens
        if token and _has_content(token) and token not in STOP_WORDS
    ]
