"""个人记忆存储工厂：按配置创建 KnowledgeStore 实现。

双模式（与温层一致）：
- backend="memory" → InMemoryKnowledgeStore（零依赖，默认，开发/测试/演示）
- backend="qdrant" → QdrantKnowledgeStore
    · local_path=":memory:" / "<目录>"（本地嵌入式，无 Docker）
    · url=...（评审 docker 内网 / 开发云 Qdrant）

上层（知识库门面 / API 路由）只依赖 KnowledgeStore 接口。
"""

from app.memory.knowledge.base import KnowledgeStore
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.knowledge.qdrant_store import QdrantKnowledgeStore
from app.memory.warm.embedding import EmbeddingProvider


def create_knowledge_store(
    backend: str = "memory",
    *,
    provider: EmbeddingProvider | None = None,
    url: str | None = None,
    api_key: str | None = None,
    local_path: str | None = None,
) -> KnowledgeStore:
    """按 backend 创建个人记忆存储。

    参数:
        backend: memory（默认，零依赖）| qdrant；
        provider: embedding 提供者（缺省用确定性本地实现）；
        url / api_key / local_path: qdrant 后端的连接参数。
    """
    name = (backend or "memory").strip().lower()
    if name in {"memory", "inmemory", "in-memory"}:
        return InMemoryKnowledgeStore(provider)
    if name in {"qdrant", "qdrant-local"}:
        # 与温层一致的便捷别名：未给 url 时走本地嵌入式内存模式
        if name == "qdrant-local" and not url and not local_path:
            local_path = ":memory:"
        return QdrantKnowledgeStore(
            provider,
            url=url,
            api_key=api_key,
            local_path=local_path,
        )
    raise ValueError(f"未知个人记忆后端：{backend!r}（可选 memory | qdrant）")
