"""一次性探针：跑一遍「酒馆世界书 → 个人知识库 → 混合检索召回」。

用法（在 backend 目录下执行）：

    ..\\.venv\\Scripts\\python.exe scripts\\probe_worldbook_recall.py overview
    ..\\.venv\\Scripts\\python.exe scripts\\probe_worldbook_recall.py recall

- overview：只读数据源快照，打印规模与「(作用域, 来源文件)」分组，不编码向量。
- recall  ：真正跑一次同步 + 检索（用 .env 里配置的 embedding provider）。

脚本**不写任何持久化存储**：recall 用内存知识库，跑完即弃，
不会污染真实 Qdrant 库。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings, plugin_search_dirs  # noqa: E402
from app.memory.knowledge import create_knowledge_store  # noqa: E402
from app.memory.knowledge.datasource_sync import (  # noqa: E402
    group_worldbook_entries,
    sync_worldbook_knowledge,
)
from app.memory.knowledge.retriever import KnowledgeRetriever  # noqa: E402
from app.memory.knowledge.scopes import TAVERN_SCOPE  # noqa: E402
from app.memory.warm.embedding import create_embedding_provider  # noqa: E402
from app.plugins.builtin import register_all_builtin  # noqa: E402
from app.rag.retrieval.bm25 import BM25Index  # noqa: E402
from app.rag.retrieval.hybrid import reciprocal_rank_fusion  # noqa: E402
from app.plugins.datasources import read_snapshots  # noqa: E402
from app.plugins.manager import PluginManager, set_plugin_manager  # noqa: E402
from app.plugins.registry import get_registry, set_registry  # noqa: E402
from app.prompts.persona.datasource_map import persona_id_for  # noqa: E402


def _bootstrap_plugins():
    """复刻 main.py 的插件静态阶段 + setup，让 read_snapshots() 能拿到真实数据。"""
    settings = get_settings()
    set_registry(None)
    registry = get_registry()
    register_all_builtin(registry)
    manager = PluginManager(
        registry,
        data_dir=Path(settings.plugins_dir).parent,
        plugin_dirs=plugin_search_dirs(settings),
    )
    manager.discover()
    set_plugin_manager(manager)
    setup_warnings = manager.setup_all()
    return settings, manager, setup_warnings


def _load_snapshot():
    settings, _manager, setup_warnings = _bootstrap_plugins()
    snapshots, read_warnings = read_snapshots()
    return settings, snapshots, [*setup_warnings, *read_warnings]


def cmd_overview() -> None:
    _settings, snapshots, warnings = _load_snapshot()
    print("=" * 72)
    print("插件 setup 警告：", warnings or "无")
    for snapshot in snapshots:
        print(
            f"数据源 {type(snapshot).__name__}："
            f"条目 {len(snapshot.entries)} / 角色 {len(snapshot.characters)} / "
            f"会话 {len(snapshot.sessions)}"
        )
        for warning in snapshot.warnings[:10]:
            print("  数据源警告：", warning)

    groups: dict[tuple[str, str], list] = defaultdict(list)
    orphaned = 0
    for snapshot in snapshots:
        grouped, orphan = group_worldbook_entries(snapshot)
        orphaned += orphan
        for key, entries in grouped.items():
            groups[key].extend(entries)

    print("-" * 72)
    print(f"分组数（(作用域, 来源文件)）：{len(groups)}，无法归属：{orphaned}")
    for (scope, source_file), entries in sorted(groups.items()):
        print(f"\n[{scope}] {source_file} —— {len(entries)} 条")
        for entry in entries[:8]:
            title = entry.title.strip() or "(无标题)"
            preview = entry.content.strip().replace("\n", " ")[:48]
            print(f"    · {title} | {preview}")
        if len(entries) > 8:
            print(f"    … 其余 {len(entries) - 8} 条省略")


def _make_provider(settings):
    return create_embedding_provider(
        settings.embedding_provider,
        api_key=settings.embedding_api_key,
        model=settings.embedding_model,
        base_url=settings.embedding_base_url,
        dimension=settings.embedding_dim,
        timeout=settings.embedding_timeout,
    )


def _make_store(settings, backend: str):
    """按后端创建知识库存储；qdrant 用 .env 的 QDRANT_URL / QDRANT_API_KEY。

    与线上 `app/api/knowledge.get_knowledge_store()` 同一套参数口径
    （后端跟随温层配置），只是这里显式指定，便于两种后端对照。
    """
    provider = _make_provider(settings)
    if backend == "qdrant":
        return create_knowledge_store(
            "qdrant",
            provider=provider,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
        )
    return create_knowledge_store("memory", provider=provider)


def cmd_diag(backend: str = "memory", *, do_sync: bool = True) -> None:
    """诊断：只看**稠密通道**对全部分块的排序，定位「该中的没中」的原因。"""
    settings, snapshots, _warnings = _load_snapshot()
    store = _make_store(settings, backend)
    print(f"存储后端：{backend}")
    if do_sync:
        print("同步结果：", sync_worldbook_knowledge(store, snapshots=snapshots).as_dict())
    else:
        print("（未同步，直接读现有库）")

    for query in ("她平时喜欢穿什么衣服？", "凌颜玉", "世界观"):
        hits = store.search(TAVERN_SCOPE, query, top_k=100)
        print("=" * 72)
        print(f"▸ 稠密通道全排序：{query}")
        for rank, hit in enumerate(hits, 1):
            title = hit.chunk.text.splitlines()[0].strip("[]")
            flag = "  ★ 衣柜" if "衣柜" in title else ""
            print(f"  {rank:2d}. sim={hit.raw_similarity:.4f}  {title}{flag}")


def cmd_recall(backend: str = "memory", *, do_sync: bool = True) -> None:
    settings, snapshots, warnings = _load_snapshot()
    if warnings:
        print("setup 完成插件：", warnings)

    print(f"embedding provider：{settings.embedding_provider} / {settings.embedding_model}")
    print(f"存储后端：{backend}", end="")
    if backend == "qdrant":
        print(f"（{settings.qdrant_url}）")
    else:
        print()

    store = _make_store(settings, backend)
    if do_sync:
        result = sync_worldbook_knowledge(store, snapshots=snapshots)
        print("=" * 72)
        print("同步结果：", result.as_dict())
    else:
        print("（未同步，直接读现有库）")

    retriever = KnowledgeRetriever(
        store,
        top_k=settings.knowledge_top_k,
        candidate_n=settings.knowledge_candidate_n,
        rrf_k=settings.knowledge_rrf_k,
    )

    # 以角色卡为单位跑召回（全局书 tavern 对任意角色都可召回）。
    # 只取第一张：当前 4 张卡都没有内嵌书，召回结果必然一致，
    # 多跑几遍只是重复调 embedding（该接口延迟不稳，能省则省）。
    characters = [c for s in snapshots for c in s.characters][:1]
    for character in characters:
        companion_id = persona_id_for(character)
        print("=" * 72)
        print(f"角色：{character.name}  (companion_id={companion_id})")
        for scope in (TAVERN_SCOPE, f"tavern:{companion_id}"):
            docs = store.list_documents(scope)
            titles = [d.title for d in docs if d.companion_id == scope]
            print(f"  作用域 {scope}：{len(docs)} 篇 {titles}")
        queries = _sample_queries(store, companion_id)
        for index, query in enumerate(queries):
            hits = retriever.retrieve(companion_id, query, include_tavern=True)
            print(f"\n  ▸ query：{query}")
            if not hits:
                print("      （无召回）")
            for hit in hits:
                text = hit.chunk.text.replace("\n", " ")
                print(
                    f"      - [{hit.chunk.companion_id}] score={hit.score:.5f} "
                    f"sim={hit.raw_similarity:.3f} :: {text[:60]}"
                )
            # 桌宠模式对照：酒馆来源应当召不回（只跑一次，避免重复调 embedding）
            if index == 0:
                pet_hits = retriever.retrieve(companion_id, query, include_tavern=False)
                print(f"      （桌宠模式对照召回 {len(pet_hits)} 条）")


#: 与具体世界书无关的自然提问，用来验证「问什么召回什么」的语义通道
_NATURAL_QUERIES = (
    "介绍一下这个世界的背景设定",
    "她平时喜欢穿什么衣服？",
)


def _sample_queries(store, companion_id: str) -> list[str]:
    """标题自命中（验证字面通道）+ 自然提问（验证语义通道）。"""
    queries: list[str] = []
    for scope in (TAVERN_SCOPE, f"tavern:{companion_id}"):
        for chunk in store.list_chunks(scope)[:2]:
            title = chunk.text.splitlines()[0].strip("[]")
            if title and title not in queries:
                queries.append(title)
    queries.extend(_NATURAL_QUERIES)
    return queries


#: 评测集：query → 期望命中的条目标题（进 top3 即算命中）。
#: 措辞刻意避开条目原文，用来区分「语义通道」与「字面通道」谁在起作用。
_GOLDEN: tuple[tuple[str, str], ...] = (
    ("她平时喜欢穿什么衣服？", "凌颜玉的衣柜"),
    ("这个世界的背景设定是什么", "世界观"),
    ("柳青是谁", "柳青"),
    ("凌颜玉的性格是怎样的", "凌颜玉的性格调色"),
    ("故事里还有哪些角色", "角色速览"),
    ("服装描写有什么要求", "服装描述规则"),
    ("互动和剧情推进的机制", "互动规则"),
    ("凌颜玉的三面性", "凌颜玉的三面性（偏含蓄）"),
)


def _weighted_rrf(
    channels: list[tuple[list[str], float]], *, k: int = 60
) -> list[tuple[str, float]]:
    """带权 RRF：`score(d) = Σ w_i / (k + rank_i(d))`。

    官方 `reciprocal_rank_fusion` 是等权版（这是 RRF 论文的原始定义）。
    这里内联一份带权实现，用来验证「降低稀疏通道权重」能不能缓解
    「泛词命中压过语义相关」的问题。
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for ranking, weight in channels:
        seen: set[str] = set()
        for rank, chunk_id in enumerate(ranking, start=1):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
            if chunk_id not in first_seen:
                first_seen[chunk_id] = order
                order += 1
    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))


def cmd_compare(backend: str = "memory", *, do_sync: bool = True) -> None:
    """三种检索策略对照：纯向量 / 等权混合（现状）/ 加权混合（向量×2）。"""
    settings, snapshots, _warnings = _load_snapshot()
    store = _make_store(settings, backend)
    if do_sync:
        sync_worldbook_knowledge(store, snapshots=snapshots)

    top_k = settings.knowledge_top_k
    cand = settings.knowledge_candidate_n
    rrf_k = settings.knowledge_rrf_k

    chunks = store.list_chunks(TAVERN_SCOPE)
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    index = BM25Index()
    for chunk in chunks:
        index.add(chunk.chunk_id, chunk.text)

    def title_of(chunk_id: str) -> str:
        return by_id[chunk_id].text.splitlines()[0].strip("[]")

    print(f"存储后端：{backend}｜top_k={top_k} candidate_n={cand} rrf_k={rrf_k}")
    print(f"语料：{len(chunks)} 个分块｜评测集：{len(_GOLDEN)} 条 query")

    tally = {"纯向量": 0, "等权混合": 0, "加权混合": 0}
    for query, expected in _GOLDEN:
        dense = [h.chunk.chunk_id for h in store.search(TAVERN_SCOPE, query, top_k=cand)]
        sparse = [cid for cid, _score in index.search(query, top_k=cand)]

        plans = {
            "纯向量": dense[:top_k],
            "等权混合": [
                cid
                for cid, _ in reciprocal_rank_fusion([dense, sparse], k=rrf_k)
            ][:top_k],
            "加权混合": [
                cid
                for cid, _ in _weighted_rrf(
                    [(dense, 2.0), (sparse, 1.0)], k=rrf_k
                )
            ][:top_k],
        }

        print("=" * 72)
        print(f"▸ {query}    期望：{expected}")
        for label, ids in plans.items():
            titles = [title_of(cid) for cid in ids]
            hit = "✓" if expected in titles else "✗"
            tally[label] += expected in titles
            print(f"   {label}  {hit}  {' ｜ '.join(titles)}")

    print("=" * 72)
    total = len(_GOLDEN)
    print("top3 命中率：")
    for label, hit in tally.items():
        print(f"   {label}：{hit}/{total}")


def main() -> None:
    args = sys.argv[1:]
    mode = args[0] if args else "overview"
    backend = args[1] if len(args) > 1 and not args[1].startswith("-") else "memory"
    # 内存库每次都是空的，必须同步；真实 Qdrant 默认**只读**，
    # 要写库得显式加 --sync（避免顺手 prune 掉用户已有的数据）。
    do_sync = ("--sync" in args) or backend == "memory"

    if mode == "overview":
        cmd_overview()
    elif mode == "recall":
        cmd_recall(backend, do_sync=do_sync)
    elif mode == "diag":
        cmd_diag(backend, do_sync=do_sync)
    elif mode == "compare":
        cmd_compare(backend, do_sync=do_sync)
    else:
        raise SystemExit(
            f"未知模式：{mode}（可选 overview | recall | diag | compare）"
        )


if __name__ == "__main__":
    main()
