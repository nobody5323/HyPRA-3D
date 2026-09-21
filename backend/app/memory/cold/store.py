"""冷层存储接口（抽象）。

写入时机：回复完成后事件驱动抽取（设计参照③）——本层只定义存储能力，
抽取逻辑（LLM 调用）在模型接入后于 memory/cold/extract.py 落地。
隔离：每个陪伴对象独立表（companion_id 命名空间，参照⑧）。
"""

from abc import ABC, abstractmethod

from app.memory.cold.models import Fact, FactStatus, FactType


class ColdMemoryStore(ABC):
    """结构化事实 + 摘要的存储抽象。

    当前实现：SQLite（sqlite_store.py）。接口收敛后便于换存储后端。
    """

    @abstractmethod
    def save_fact(self, companion_id: str, fact: Fact) -> str:
        """持久化一条事实（自动生成 fact_id），返回 fact_id。"""

    @abstractmethod
    def list_facts(
        self,
        companion_id: str,
        *,
        type: FactType | None = None,
        status: FactStatus = FactStatus.ACTIVE,
        limit: int = 50,
    ) -> list[Fact]:
        """按条件列出事实（默认仅活跃），按 importance 优先、created_at 次之降序。"""

    @abstractmethod
    def get_fact(self, companion_id: str, fact_id: str) -> Fact | None:
        """按 id 取单条事实。"""

    @abstractmethod
    def update_status(
        self,
        companion_id: str,
        fact_id: str,
        status: FactStatus,
    ) -> bool:
        """更新事实状态（事件闭环用），返回是否更新成功。"""

    @abstractmethod
    def touch(self, companion_id: str, fact_id: str) -> bool:
        """印证事实（刷新 last_seen_at），返回是否成功。"""

    @abstractmethod
    def delete_fact(self, companion_id: str, fact_id: str) -> bool:
        """删除一条事实（误抽取纠正用）。"""
