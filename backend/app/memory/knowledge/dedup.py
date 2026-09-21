"""文档去重（个人记忆层）：文件 MD5 → 内容 MD5 → MinHash 近似判重。

**为什么需要三级**：

| 级别 | 判定对象 | 能抓住什么 | 局限 |
|---|---|---|---|
| 文件 MD5 | 原始字节 | 同一文件重复上传 | 改个文件名就失效 |
| 内容 MD5 | 清洗后文本 | 同内容不同格式（.md 导出为 .txt） | 改一个字就失效 |
| **MinHash** | shingle 集合 | **近似重复**（改了几个字的版本） | 概率性，有误判余地 |

前两级是精确哈希：命中即重复，零误判，且极快（解析前就能判掉一级）。
但精确哈希对「通篇一样只改了三处」完全失效——这正是 MinHash 的用武之地。

**为什么自己实现 MinHash 而不用 datasketch**：本项目的知识库是单用户量级
（几十到几百篇），O(n) 两两比较足够；而 datasketch 的主要价值是 LSH 索引
（为上百万文档加速），用在这里是杀鸡用牛刀，白搭一个依赖。

**阈值语义**：Jaccard 是概率估计，128 个哈希的估计误差约 1/√128 ≈ 8.8%。
因此命中判重时**不自动覆盖**，而是返回相似文档信息交由用户决定（force 参数）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

#: MinHash 哈希个数：估计误差约 1/sqrt(128) ≈ 8.8%
DIGEST_SIZE = 128
#: shingle 长度（字符 n-gram）。中文场景取 5：既保证区分度，又能容忍小幅改写。
SHINGLE_SIZE = 5
#: 近似重复的默认判定阈值
DEFAULT_SIMILARITY_THRESHOLD = 0.85
#: Mersenne 素数 2^61-1：取模运算快且哈希分布均匀
_MODULUS = (1 << 61) - 1


# ---------- 精确去重 ----------


def file_digest(data: bytes) -> str:
    """文件字节的 MD5（一级：同一文件重复上传）。"""
    return hashlib.md5(data).hexdigest()


def content_digest(text: str) -> str:
    """清洗后文本的 MD5（二级：同内容不同格式或文件名）。

    先做空白归一：换行风格（CRLF/LF）与多余空白的差异不应被算作不同内容。
    """
    normalized = " ".join((text or "").split())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


# ---------- 近似去重（MinHash）----------


@dataclass(frozen=True)
class MinHashSignature:
    """文本的 MinHash 签名（定长整数数组，可持久化到存储层）。"""

    values: tuple[int, ...]

    def __len__(self) -> int:
        return len(self.values)


def _shingles(text: str, k: int = SHINGLE_SIZE) -> set[str]:
    """字符 k-gram 集合（**忽略所有空白**）。

    取舍：中文场景下换行与缩进并不代表词边界，忽略空白能让同一文档的不同排版
    产生相同签名（去重对排版差异鲁棒）。代价是英文的词边界信息也会丢失，
    但 5-gram 仍足以区分不同句子。
    """
    compact = "".join((text or "").split())
    if not compact:
        return set()
    if len(compact) <= k:
        return {compact}
    return {compact[i : i + k] for i in range(len(compact) - k + 1)}


def _coefficients(count: int, *, seed: bytes = b"hypra-minhash") -> list[tuple[int, int]]:
    """确定性生成线性同余哈希族 `h(x) = (a·x + b) mod p` 的系数。

    用 blake2b 派生而非 random 模块：与 Python 版本的随机数实现无关，
    跨环境、跨版本稳定（签名必须可复现，否则去重结果会漂移）。
    """
    pairs: list[tuple[int, int]] = []
    for i in range(count):
        digest = hashlib.blake2b(seed + i.to_bytes(4, "big"), digest_size=16).digest()
        a = int.from_bytes(digest[:8], "big") % (_MODULUS - 1) + 1
        b = int.from_bytes(digest[8:], "big") % _MODULUS
        pairs.append((a, b))
    return pairs


def minhash_signature(text: str, *, count: int = DIGEST_SIZE) -> MinHashSignature:
    """计算文本的 MinHash 签名。

    实现用「一次基础哈希 + 线性同余族」而不是「每个 shingle 算 N 次哈希」：
    每 shingle 只做一次 blake2b，其余是整数乘加取模，实测快一个数量级。
    """
    shingles = _shingles(text)
    if not shingles:
        return MinHashSignature(tuple([0] * count))

    bases = [
        int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")
        for s in shingles
    ]
    return MinHashSignature(
        tuple(
            min((base * a + b) % _MODULUS for base in bases)
            for a, b in _coefficients(count)
        )
    )


def jaccard_estimate(left: MinHashSignature, right: MinHashSignature) -> float:
    """由两个签名估计原 shingle 集合的 Jaccard 相似度（相等分量占比）。"""
    if len(left) != len(right):
        raise ValueError(f"签名长度不一致：{len(left)} vs {len(right)}")
    if not len(left):
        return 0.0
    same = sum(1 for x, y in zip(left.values, right.values) if x == y)
    return same / len(left)


def find_similar(
    signature: MinHashSignature,
    candidates: Iterable[tuple[str, MinHashSignature]],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[tuple[str, float]]:
    """从候选中找出与给定签名相似度 ≥ threshold 的项。

    返回 `[(doc_id, 相似度)]`，按相似度降序、同分按 doc_id 稳定排序。
    """
    scored = [
        (doc_id, jaccard_estimate(signature, candidate))
        for doc_id, candidate in candidates
    ]
    return sorted(
        (item for item in scored if item[1] >= threshold),
        key=lambda item: (-item[1], item[0]),
    )
