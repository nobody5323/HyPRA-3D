"""分块器接口与共用切分骨架。

拆分自原 `chunker.py`（`AGENTS.md §9.11` P2）：**「在哪里切」是可替换的策略**，
「怎么保证块长可控」是共用骨架。

原实现的判断不变——**为什么是融合而不是二选一**：

- 纯递归切分：块长可控，但切分点只看标点，会在语义连贯处硬切；
- 纯语义切分（相邻句向量相似度骤降处切）：切点合理，但**不保证块长可控**，
  可能切出 30 字的碎片或 2000 字的巨块，撞爆 token 预算。

所以职责分工是：**递归切分保证长度可控，语义相似度决定在哪里「提前」切**。

    text
     ↓ ① 四级递归降级：Markdown 标题 → 段落 → 句子 → 子句 → 硬切
    units（语义单元，每个 ≤ max_chars）
     ↓ ② 合并过短单元（避免碎片块）
     ↓ ③ 贪心聚合到 target；若遇语义低谷（策略给出）则提前切
    chunks
     ↓ ④ 句级 overlap（回退完整句子，而不是截断的字符）

①②④ 在基类的模板方法 `Chunker.split()` 里，与策略无关；
**只有 ③ 里的「低谷在哪」是策略**——这正是 `boundaries()` 这一个抽象方法的形状。
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

#: 聚合目标长度（字符）。中文 1 字 ≈ 0.7 token，400 字 ≈ 280 tokens
DEFAULT_TARGET = 400
#: 单块硬上限（含 overlap 前的正文）
DEFAULT_MAX_CHARS = 600
#: 尾部整句回退的累计上限
DEFAULT_OVERLAP = 80
#: 短于此长度的单元并入相邻单元
DEFAULT_MIN_CHUNK = 60
#: 相邻单元余弦相似度低于此值即视为语义低谷
DEFAULT_SEMANTIC_THRESHOLD = 0.75

#: 一级边界：Markdown 标题（`#` 开头）——标题开启新块
_HEADING = r"^#{1,6}\s"
#: 二级边界：段落（空行）
_PARAGRAPH = "\n\n"
#: 三级边界：句末标点（**零宽断言**：切分后标点仍归前一句）
_SENTENCE_END = r"(?<=[。！？；!?;])"
#: 四级边界：子句标点
_CLAUSE_END = r"(?<=[，、,])"


def split_keep(text: str, pattern: str) -> list[str]:
    """按零宽断言切分并丢弃空段（标点归属前一段）。"""
    return [part for part in re.split(pattern, text) if part and part.strip()]


def split_by_heading(text: str) -> list[str]:
    """一级切分：Markdown 标题开启新块。"""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if re.match(_HEADING, line) and current:
            blocks.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def split_long(text: str, max_chars: int) -> list[str]:
    """三 / 四级切分：句子 → 子句 → 硬切。"""
    units: list[str] = []
    for sentence in split_keep(text, _SENTENCE_END):
        if len(sentence) <= max_chars:
            units.append(sentence)
            continue
        for clause in split_keep(sentence, _CLAUSE_END):
            if len(clause) <= max_chars:
                units.append(clause)
            else:
                # 兜底：没有标点的长串（如代码、无断句的长文本）只能硬切
                units.extend(
                    clause[i : i + max_chars] for i in range(0, len(clause), max_chars)
                )
    return units


def split_units(text: str, max_chars: int) -> list[str]:
    """四级递归降级，切出每个都 ≤ max_chars 的语义单元。"""
    units: list[str] = []
    for block in split_by_heading(text):
        for paragraph in block.split(_PARAGRAPH):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if len(paragraph) <= max_chars:
                units.append(paragraph)
            else:
                units.extend(split_long(paragraph, max_chars))
    return units


def merge_short(units: list[str], min_chunk: int) -> list[str]:
    """把过短的单元并入相邻单元，避免产生碎片块。"""
    merged: list[str] = []
    for unit in units:
        if merged and len(merged[-1]) < min_chunk:
            merged[-1] = f"{merged[-1]}\n{unit}"
        else:
            merged.append(unit)
    # 尾部过短的并入前一个
    if len(merged) > 1 and len(merged[-1]) < min_chunk:
        merged[-2] = f"{merged[-2]}\n{merged.pop()}"
    return merged


def pack(
    units: list[str],
    *,
    target: int,
    max_chars: int,
    min_chunk: int,
    boundaries: set[int],
) -> list[str]:
    """贪心聚合语义单元成块。

    `boundaries` 里的整数 i 表示「可以在 `units[i]` **之前**切分」——
    由策略决定（纯长度策略返回空集）。
    """
    chunks: list[str] = []
    current: list[str] = []
    length = 0

    for index, unit in enumerate(units):
        unit_length = len(unit)
        should_cut = False
        if current:
            if length + unit_length > max_chars:
                should_cut = True                      # ① 硬约束
            elif length >= target:
                should_cut = True                      # ② 已达目标
            elif index in boundaries and length >= min_chunk:
                should_cut = True                      # ③ 语义低谷且已有足够内容

        if should_cut:
            chunks.append("\n".join(current))
            current, length = [], 0

        current.append(unit)
        length += unit_length

    if current:
        chunks.append("\n".join(current))
    return chunks


def tail_sentences(text: str, overlap: int) -> str:
    """从尾部按**完整句子**回退，累计到 ≈ overlap 字符。"""
    sentences = split_keep(text, _SENTENCE_END)
    picked: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        if total >= overlap:
            break
        picked.append(sentence)
        total += len(sentence)
    if not picked:
        # 整块没有句末标点：退化为尾部截断，保证 overlap 仍然生效
        return text[-overlap:] if len(text) > overlap else text
    return "".join(reversed(picked)).strip()


def apply_overlap(chunks: list[str], overlap: int) -> list[str]:
    """为每块补上上一块尾部的完整句子。"""
    if overlap <= 0 or len(chunks) <= 1:
        return chunks

    result = [chunks[0]]
    for previous, current in zip(chunks, chunks[1:]):
        tail = tail_sentences(previous, overlap)
        result.append(f"{tail}\n{current}" if tail else current)
    return result


class Chunker(ABC):
    """分块器接口（一种「在哪里切」的策略一个实现）。"""

    #: 实现名（与配置值一致，供调试与能力索引展示）
    name: str = ""

    @abstractmethod
    def boundaries(self, units: list[str]) -> set[int]:
        """给出「可以在 `units[i]` **之前**切分」的位置集合。

        返回空集即「只看长度、不看语义」——`PlainChunker` 就是这样。
        """

    def split(
        self,
        text: str,
        *,
        target: int = DEFAULT_TARGET,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap: int = DEFAULT_OVERLAP,
        min_chunk: int = DEFAULT_MIN_CHUNK,
    ) -> list[str]:
        """模板方法：校验 → 四级切分 → 合并短单元 → 求边界 → 聚合 → 补 overlap。

        共用的四步放基类，实现只管 `boundaries()`——否则每加一种策略都要复制
        这四步，而且很容易漏掉其中某一步（尤其 overlap 与短单元合并）。
        """
        if not text or not text.strip():
            return []
        if max_chars <= 0:
            raise ValueError("max_chars 必须为正数")
        if target <= 0:
            raise ValueError("target 必须为正数")

        units = merge_short(split_units(text, max_chars), min_chunk)
        chunks = pack(
            units,
            target=target,
            max_chars=max_chars,
            min_chunk=min_chunk,
            boundaries=self.boundaries(units),
        )
        return apply_overlap(chunks, overlap)
