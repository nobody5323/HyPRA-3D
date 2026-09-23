"""字符 bigram 分词器：零依赖降级实现。

中文取单字 + 双字，英文/数字整体小写。精度不如 jieba（「压力」会被切成
「压」「力」「压力」三个 token，双字项仍能命中），但保证离线与 CI 环境下
检索链路不中断。
"""

from __future__ import annotations

from app.rag.retrieval.tokenize.base import CJK_RUN, STOP_WORDS, WORD, Tokenizer


class BigramTokenizer(Tokenizer):
    """无 jieba 时的降级分词。"""

    name = "char-bigram"

    def tokenize(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []

        tokens: list[str] = list(WORD.findall(text.lower()))
        for run in CJK_RUN.findall(text):
            tokens.extend(ch for ch in run if ch not in STOP_WORDS)       # 单字
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))    # 双字
        return tokens
