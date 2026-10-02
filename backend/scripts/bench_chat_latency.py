"""对话链路耗时探针：把「这一轮慢在哪」量化出来。

用法（在 backend/ 下）：

    ../.venv/Scripts/python.exe scripts/bench_chat_latency.py
    ../.venv/Scripts/python.exe scripts/bench_chat_latency.py --turns 5
    ../.venv/Scripts/python.exe scripts/bench_chat_latency.py --warm qdrant   # 接真实向量库

默认走**离线**依赖（内存向量库 + 内存知识库 + mock LLM），不碰网络、不花钱，
用来回答两个问题：

1. **各阶段占比**：召回 / 组装 / 生成各占多少毫秒，瓶颈在召回还是在模型？
2. **embedding 被重复调用了几次**：一轮对话会对同一句用户输入编码 4–7 次
   （世界书 1 + 知识库每作用域 1 + 温层 1），且串行执行。脚本分别跑「不缓存」
   与「缓存」两遍，把省下的次数与时间直接打出来。

注意：离线跑用的是 mock LLM（几乎零延迟），所以**它测不出模型的真实生成耗时**
——那部分要看真实端点的 `generate_reply` 占比（线上响应里的 `timings` 字段）。
本脚本的价值在于把「模型之外的固定开销」量出来：这部分是纯浪费，必须归零。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

# 允许直接以脚本方式运行（scripts/ 不在包路径里）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.chat_graph import build_chat_graph  # noqa: E402
from app.graph.nodes import ChatNodes  # noqa: E402
from app.llm.mock import MockLLMProvider  # noqa: E402
from app.memory.cold.sqlite_store import SqliteColdStore  # noqa: E402
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore  # noqa: E402
from app.memory.knowledge.retriever import KnowledgeRetriever  # noqa: E402
from app.memory.store import MemoryStore  # noqa: E402
from app.memory.warm.embedding import (  # noqa: E402
    CachedEmbeddingProvider,
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
)
from app.memory.warm.inmemory_store import InMemoryWarmStore  # noqa: E402
from app.prompts.persona.loader import load_builtin_presets  # noqa: E402
from app.worldbook.loader import load_builtin_entries  # noqa: E402

PERSONA_ID = "therapist-elder-sister"

#: 模拟一轮对话的输入（每轮换一句，避免缓存跨轮命中掩盖问题）
PROMPTS = [
    "我最近总是失眠，躺下就开始想工作的事",
    "今天开会被点名了，我当场脑子一片空白",
    "我妈又打电话催我结婚，挂完电话特别烦",
    "周末一个人在家，突然觉得挺没意思的",
    "其实我知道该早点睡，但就是放不下手机",
    "刚跑完步，心情好多了",
]


class _CountingEmbedding(EmbeddingProvider):
    """转发给内层 provider，并记录真实调用次数（用于量化重复编码）。"""

    def __init__(self, inner: EmbeddingProvider) -> None:
        self._inner = inner
        self.embed_calls = 0
        self.batch_calls = 0

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return self._inner.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        return self._inner.embed_batch(texts)


def _build(*, use_cache: bool, embedding: _CountingEmbedding, db_path: Path):
    """按「是否套缓存」构造一条完整但离线的对话图。"""
    provider: EmbeddingProvider = (
        CachedEmbeddingProvider(embedding) if use_cache else embedding
    )
    knowledge_store = InMemoryKnowledgeStore(provider)
    # 放一段用户资料，让知识召回这条路径真的被走到（否则测不出它的编码开销）
    knowledge_store.add_document(
        PERSONA_ID,
        title="我的资料",
        chunks=[
            "我住在杭州，做后端开发，最近在赶一个上线项目。",
            "我养了一只叫年糕的橘猫，它每天六点准时叫我起床。",
        ],
    )
    memory = MemoryStore(
        SqliteColdStore(db_path=db_path),
        InMemoryWarmStore(provider),
        extractor=None,
    )
    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=memory,
        knowledge=KnowledgeRetriever(knowledge_store),
        embedding_provider=provider,
        llm_provider=MockLLMProvider(),
    )
    return build_chat_graph(nodes)


def _run(graph, turns: int) -> tuple[list[dict[str, float]], float]:
    """跑 turns 轮，返回每轮的节点耗时与总耗时（毫秒）。"""
    per_turn: list[dict[str, float]] = []
    started = time.perf_counter()
    for index in range(turns):
        text = PROMPTS[index % len(PROMPTS)]
        result = graph.invoke(
            {
                "session_id": "bench",
                "companion_id": PERSONA_ID,
                "persona_id": PERSONA_ID,
                "user_name": "小林",
                "user_input": text,
                "history": [],
                "turn_index": index + 1,
                "state_vars": {"current_mood": "平静"},
            }
        )
        per_turn.append(result.get("timings", {}))
    return per_turn, (time.perf_counter() - started) * 1000.0


def _report(title: str, per_turn, total_ms: float, calls: int, turns: int) -> None:
    print(f"\n=== {title} ===")
    print(f"总耗时 {total_ms:.0f}ms / {turns} 轮（平均 {total_ms / turns:.0f}ms）")
    print(f"embedding 真实请求次数 {calls}（平均 {calls / turns:.1f} 次/轮）")

    stages = [
        "load_persona",
        "worldbook_recall",
        "knowledge_recall",
        "memory_recall",
        "assemble_prompt",
        "generate_reply",
    ]
    print(f"{'阶段':<20}{'平均ms':>10}{'最大ms':>10}")
    for stage in stages:
        values = [turn.get(stage, 0.0) for turn in per_turn]
        if not values:
            continue
        print(f"{stage:<20}{statistics.mean(values):>10.1f}{max(values):>10.1f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="对话链路耗时探针")
    parser.add_argument("--turns", type=int, default=3, help="跑多少轮（默认 3）")
    args = parser.parse_args()

    turns = max(1, args.turns)
    print(f"对话链路耗时探针：{turns} 轮 / 离线依赖（内存向量库 + mock LLM）")
    print("提示：mock LLM 几乎零延迟，此处测的是**模型之外的固定开销**。")

    results = []
    for use_cache in (False, True):
        # 用确定性本地实现替代远程 embedding：这里要量的是「调用了几次」，
        # 不是「单次多慢」。真实端点的单次耗时按 200–500ms 估算即可。
        embedding = _CountingEmbedding(DeterministicEmbeddingProvider())
        # 每遍都用全新的冷层库：残留的记忆会让第二遍多召回几条，
        # 两遍的基线就不一致了
        db_path = Path(f"data/bench_{'cached' if use_cache else 'raw'}.db")
        db_path.unlink(missing_ok=True)
        graph = _build(use_cache=use_cache, embedding=embedding, db_path=db_path)
        per_turn, total_ms = _run(graph, turns)
        calls = embedding.embed_calls + embedding.batch_calls
        _report(
            "启用 embedding 缓存（当前实现）" if use_cache else "未缓存（改动前）",
            per_turn,
            total_ms,
            calls,
            turns,
        )
        results.append((use_cache, calls, total_ms))

    (_, raw_calls, raw_ms), (_, cached_calls, cached_ms) = results
    saved = raw_calls - cached_calls
    print("\n=== 结论 ===")
    print(f"每轮省下 {saved / turns:.1f} 次 embedding 请求")
    print(
        f"按远程 embedding 单次 300ms 估算，每轮省 ≈ {saved / turns * 300:.0f}ms "
        "（本地实现下测不出单次耗时，故此处用估算值）"
    )
    if raw_ms > cached_ms:
        print(f"本地端到端：{raw_ms:.0f}ms → {cached_ms:.0f}ms")
    print(
        "\n注：省下的次数 = 知识库作用域数 + 温层 1 次（世界书条目若启用向量通道再多 1 次）。"
        "\n    桌宠模式 2 个作用域，酒馆模式 5 个——所以酒馆模式下这一项省得更多。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
