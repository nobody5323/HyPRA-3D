"""RRF（Reciprocal Rank Fusion）：把多个排名列表融合为一个。

**为什么用 RRF 而不是加权求和**：余弦相似度（0~1）与 BM25 分数（0~∞）量纲
完全不同，加权求和需要先做归一化并人工调参；RRF 只依赖**排名**，天然回避了
尺度问题，因此是混合检索的业界通用做法。

公式（Cormack 等，2009）：

    score(d) = Σ_i  1 / (k + rank_i(d))        rank 从 1 开始，k 默认 60

⚠️ 融合后的分数只有**相对**含义，**不能**用于绝对阈值判断——需要「相似度
不低于 X」这类约束时，必须作用在融合前的各通道上（见 MemoryStore.recall）。
"""

from collections.abc import Iterable, Sequence

#: RRF 平滑常数：越大则各排名之间的得分差异越平缓（原论文推荐 60）
DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[str]],
    *,
    k: int = DEFAULT_RRF_K,
) -> list[tuple[str, float]]:
    """融合多个「已按相关性降序排列」的 doc_id 序列。

    参数:
        rankings: 各通道的排名列表（如向量召回、BM25 召回各自的 top_n）；
        k: RRF 平滑常数（>= 0）。

    返回:
        [(doc_id, rrf_score)]，按分数降序；同分时按**首次出现顺序**稳定排序，
        使前一个通道的结果在并列时优先。
    """
    if k < 0:
        raise ValueError("RRF 的 k 必须 >= 0")

    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    seen_order = 0

    for ranking in rankings:
        seen_in_channel: set[str] = set()
        for rank, doc_id in enumerate(ranking, start=1):
            if doc_id in seen_in_channel:
                continue  # 同一通道内重复出现只计一次（防御上游重复）
            seen_in_channel.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            if doc_id not in first_seen:
                first_seen[doc_id] = seen_order
                seen_order += 1

    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
