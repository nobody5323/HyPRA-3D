"""世界书语义触发索引（三通道之第三通道）。

职责划分：
- 本模块只回答「哪些条目在语义上被当前输入命中」，返回条目 id 集合；
- 命中阈值由条目自身的 vector_threshold 决定，本模块不参与调参；
- matcher.py 负责把关键词 / 正则 / 向量三通道的结果合并。

为什么需要它（而不是在 matcher 里逐条算）：
- 条目语义文本是**静态**的，构造时一次性编码，避免每轮对话重复编码 N 条；
- 检索时 query 只编码**一次**，再与各条目向量比较，而非每条目各编码一次。

容错：无 provider、无启用向量的条目、编码失败、向量维度不一致，
一律降级为「向量通道无命中」，绝不阻断关键词与正则通道（对话不中断）。
"""

from collections.abc import Iterable

from app.memory.warm.embedding import EmbeddingProvider, cosine_similarity
from app.worldbook.models import WorldBookEntry


class WorldBookVectorIndex:
    """世界书条目向量索引（进程内，随条目加载重建）。"""

    def __init__(
        self,
        entries: Iterable[WorldBookEntry],
        provider: EmbeddingProvider | None = None,
    ) -> None:
        """构造时编码全部声明了 vector_text 的条目。

        参数:
            entries: 世界书条目全集（enabled=False 的条目也会入索引，
                是否采用由 matcher 决定——这样停用/启用条目无需重建索引）；
            provider: embedding 提供者；None 时索引为空（向量通道静默关闭）。
        """
        self._provider = provider
        self._vectors: list[tuple[WorldBookEntry, list[float]]] = []

        targets = [entry for entry in entries if entry.vector_enabled]
        if provider is None or not targets:
            return
        try:
            vectors = provider.embed_batch([entry.vector_text for entry in targets])
        except Exception:  # noqa: BLE001 - 编码失败仅关闭向量通道，不影响对话
            return
        if len(vectors) != len(targets):  # 防御：provider 返回条数不符则弃用
            return
        self._vectors = list(zip(targets, vectors))

    @property
    def indexed_count(self) -> int:
        """已成功建立向量的条目数（用于调试与健康检查）。"""
        return len(self._vectors)

    def match(self, text: str) -> set[str]:
        """返回语义命中的条目 id 集合（无命中或不可用时为空集）。"""
        if not self._vectors or not text.strip():
            return set()
        try:
            query_vector = self._provider.embed(text)
        except Exception:  # noqa: BLE001 - 检索编码失败同样降级为空命中
            return set()

        hits: set[str] = set()
        for entry, vector in self._vectors:
            try:
                score = cosine_similarity(query_vector, vector)
            except ValueError:
                continue  # 维度不一致（如切换了 embedding 模型）→ 跳过该条目
            if score >= entry.vector_threshold:
                hits.add(entry.id)
        return hits
