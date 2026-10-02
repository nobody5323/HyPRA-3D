"""温层（语义向量记忆）数据模型与存储接口。

隔离：每个陪伴对象独立 collection（参照⑧），由 companion_id 区分。
后端可替换：当前实现 InMemoryWarmStore（inmemory_store.py）用于离线开发与测试；
接入 Qdrant 时实现同一接口（qdrant_store.py，配置 URL 即可云/本地切换）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

#: 召回候选倍数：先按**原始相似度**取 top_k × N 条候选，再施加时间衰减重排。
#: 两个存储后端必须使用同一个值，否则开发（memory）与评审（qdrant）环境的
#: 召回结果会不一致。时间衰减会压低旧记忆分数，故候选集必须远大于最终条数，
#: 否则「先截断、后衰减」会让排序失真。
CANDIDATE_FACTOR = 5


@dataclass
class MemoryRecord:
    """一条已入库的向量记忆。"""

    memory_id: str
    companion_id: str
    text: str
    created_at: datetime
    #: 最近一次被召回的时间（淘汰依据）。新增时等于 created_at。
    #: 判定记忆价值用的是「是否仍在被需要」而非「有多新」——
    #: 一条六个月前写下但每周都会被召回的记忆，不该被淘汰。
    last_recalled_at: datetime | None = None
    metadata: dict = field(default_factory=dict)
    vector: list[float] = field(default_factory=list, repr=False)


@dataclass
class SearchResult:
    """一次语义检索的命中结果。"""

    record: MemoryRecord
    score: float = 0.0          # 组合分（相似度 × 时间衰减）
    raw_similarity: float = 0.0  # 纯余弦相似度（调试/对比用）


class WarmMemoryStore(ABC):
    """温层向量记忆存储抽象。

    add/search/delete 均以 companion_id 隔离命名空间；
    search 支持时间衰减重排参数（参照⑥），默认半衰期与衰减强度可调。
    """

    @abstractmethod
    def add(
        self,
        companion_id: str,
        text: str,
        *,
        metadata: dict | None = None,
        memory_id: str | None = None,
        created_at: datetime | None = None,
    ) -> str:
        """入库一条记忆（内部完成 embedding），返回 memory_id。

        created_at 可选（缺省为当前时间）：显式传入供导入历史数据与测试
        注入时间使用——两个存储后端必须都支持，否则时间衰减行为无法验证。
        """

    @abstractmethod
    def search(
        self,
        companion_id: str,
        query: str,
        *,
        top_k: int = 5,
        half_life_days: float = 30.0,
        decay_exponent: float = 1.0,
        now: datetime | None = None,
    ) -> list[SearchResult]:
        """语义检索：query 编码后与集合内记忆算相似度，
        按「相似度 × 时间衰减」组合分排序返回 top_k。
        now 参数仅供测试注入时间。
        """

    @abstractmethod
    def delete(self, companion_id: str, memory_id: str) -> bool:
        """删除一条记忆，返回是否删除成功。"""

    @abstractmethod
    def clear_scope(self, companion_id: str) -> int:
        """删除该陪伴对象的**全部**记忆，返回被删除的条数。

        用途：用户要「彻底忘掉这个角色」（与删掉单条记忆是两件事）。
        - 该陪伴对象没有记忆时返回 0，**不报错**（清空一个已经空的库不是错误，
          调用方也不需要先查一遍）；
        - 这是**不可恢复**操作，调用方（API 层 / 界面）必须做二次确认。
        """

    @abstractmethod
    def count(self, companion_id: str) -> int:
        """某陪伴对象当前的记忆条数。"""

    @abstractmethod
    def list_records(self, companion_id: str) -> list[MemoryRecord]:
        """列出该陪伴对象的**全量**记忆记录。

        用途：构建本地 BM25 稀疏索引（需要看到全部文本才能算 IDF）。
        这是全量读取，**不要**在每轮对话的召回路径上调用——上层应缓存索引。
        """

    @abstractmethod
    def mark_recalled(self, companion_id: str, memory_ids: list[str]) -> int:
        """把指定记忆的 last_recalled_at 刷新为当前时间，返回实际更新条数。

        由上层在召回命中后调用。**这是记忆淘汰机制的前提**：
        没有这个信号，「哪些记忆还有用」就无从判断。
        """

    @abstractmethod
    def purge_expired(
        self,
        companion_id: str,
        *,
        ttl_days: float,
        now: datetime | None = None,
    ) -> list[str]:
        """删除超过 ttl_days 未被召回的记忆，返回被删除的 memory_id 列表。

        判定依据是 `max(last_recalled_at, created_at)`。

        ⚠️ TTL 必须**远大于**时间衰减半衰期：衰减会压低旧记忆分数、使其难以
        进入 top_k，若不留出重新召回的窗口，就会形成「衰减 → 不被召回 →
        被删除」的正反馈，把所有旧记忆清空。另见本文档 CANDIDATE_FACTOR 的说明。
        now 参数仅供测试注入时间。
        """
