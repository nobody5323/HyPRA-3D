"""世界书 → 个人知识库：把外部设定转成**可按需检索**的语料。

为什么不直接注入上下文：真实世界书动辄几百条（实测 274 条，其中 133 条是常驻型），
而世界书注入预算只有 400 token —— 全塞的结果是大部分被截掉，而且**每一轮都在占位**。
转成知识库后它们是**按需召回**的：问到相关的事才进上下文，不问就不占预算。

分篇规则 —— **`(作用域, 来源文件)` 各一篇**，且**每本世界书一个独立作用域**：

- 全局书（`world/<文件名>`）与角色卡内嵌书（`char/<角色名>`）**一视同仁**，
  都写进各自的 `tavern:book:{来源哈希}`（`scopes.book_scope`）；
- 「挂上哪几本、只查哪几本」由**全局挂载清单**决定（插件配置
  `tavern_mounted_books`），默认全不挂 —— 见 `scopes.retrieval_scopes`。

**每本一个作用域**（而非旧版「全局书共用一个 `tavern`、内嵌书按角色」）：
隔离单位下移到单本后，A 书的内容永不污染 B 书；用户挂哪本才生效，
换书就是换挂载，不必担心多本书互相串味。

**酒馆来源单独成库、且只在酒馆聊天模式下召回**（AGENTS.md §8.1 / §8.2）：
作用域键一律由 `app/memory/knowledge/scopes` 算出——写入侧与检索侧共用同一套函数，
两边各算各的话，内容就会挂在一个永远匹配不上的键上（表现为「明明导了却召不回」）。

`doc_id` 由来源标识算出而非随机 → 重复同步是**覆盖同一篇**，不会越同步越多；
同时清理「来源已不存在」的旧文档（在酒馆里删了世界书，这里也不该继续留着）。
清理**不能只问当前快照**：某本书删掉后它的作用域就不在快照里了，只靠快照推导
就永远清不掉（现象是「删了仍在答」）。所以清理扫的是 `discover_tavern_scopes`
——快照 ∪ 挂载清单 ∪ **存储实况**三条线索的并集。
旧版本曾把全局书写进 `tavern`、内嵌书写进 `tavern:{角色id}`，清理逻辑会顺手
把它们删掉（见 `candidate_scopes` 与 `discover_stored_tavern_scopes`）。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from app.memory.knowledge.base import KnowledgeStore
from app.memory.knowledge.scopes import TAVERN_SCOPE as LEGACY_TAVERN_SCOPE
from app.memory.knowledge.scopes import book_scope, is_legacy_tavern_scope
from app.plugins.contracts import DataSourceEntry, DataSourceSnapshot
from app.plugins.datasources import read_snapshots, stable_item_id
from app.prompts.persona.datasource_map import scopes_by_character_name
from app.worldbook.models import POSITION_OUTLET, SCOPE_ALL

logger = logging.getLogger(__name__)

#: 旧版作用域的分隔符（`tavern:{persona_id}`），仅用于清理旧数据
_LEGACY_SEP = ":"

#: 新格式作用域前缀（`tavern:book:`），供枚举下推
_BOOK_SCOPE_PREFIX = f"{LEGACY_TAVERN_SCOPE}{_LEGACY_SEP}book{_LEGACY_SEP}"

#: 知识库文档 id 的前缀（与其它 doc_id 分开，便于识别来源）
DOC_ID_PREFIX = "tbkb"

#: 写入知识库时标记的来源类型：界面据此把「酒馆世界书」与「我上传的语料」分开显示
SOURCE_TYPE = "tavern-worldbook"


@dataclass
class KnowledgeSyncResult:
    """一次同步的统计。"""

    documents: int = 0
    chunks: int = 0
    removed: int = 0
    #: 因找不到所属角色卡而没进库的条目数
    skipped: int = 0
    #: 每个知识作用域写入了多少篇
    scopes: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "removed": self.removed,
            "skipped": self.skipped,
            "scopes": self.scopes,
            "warnings": self.warnings,
        }


def _chunk_text(entry: DataSourceEntry) -> str:
    """一条世界书条目 = 一个分块。

    正文带上标题：BM25 稀疏通道靠字面命中，而标题往往就是关键词本身
    （「人物设计_×××」这类标题比正文更能对上提问用词）。
    写法与注入时的块格式一致，召回后读起来也不会突兀。
    """
    return f"[{entry.title}]\n{entry.content}"


def group_worldbook_entries(
    snapshot: DataSourceSnapshot,
) -> tuple[dict[tuple[str, str], list[DataSourceEntry]], int]:
    """把条目按 `(作用域, 来源文件)` 分组，返回 `(分组, 无法归属的条目数)`。

    来源文件由插件给出：全局书是 `world/<文件名>`，角色内嵌书是 `char/<角色名>`。
    两者都换成本书的独立作用域 `tavern:book:{哈希}`（`scopes.book_scope`）——
    不再区分「全局共享 / 角色专属」，每本都是可挂载的一本书。
    """
    groups: dict[tuple[str, str], list[DataSourceEntry]] = defaultdict(list)
    for entry in snapshot.entries:
        if not entry.content.strip():
            continue
        if entry.position == POSITION_OUTLET:
            continue  # 宏出口档不进提示词，也不该进知识库
        # source_id 形如 `world/世界甲#7` / `char/角色名#3`：去掉条目序号就是来源文件
        source_file = entry.source_id.split("#", 1)[0] or entry.source
        groups[(book_scope(source_file), source_file)].append(entry)
    return dict(groups), 0


def _doc_id(scope: str, source_file: str) -> str:
    """文档 id：同来源恒等 → 重复同步覆盖同一篇。"""
    return stable_item_id(DOC_ID_PREFIX, scope, source_file)


def _doc_title(source_file: str, count: int) -> str:
    kind, _, name = source_file.partition("/")
    label = "角色内嵌设定" if kind == "char" else "世界书"
    return f"[酒馆] {label}·{name or source_file}（{count} 条）"


def mounted_book_sources(snapshots: list[DataSourceSnapshot]) -> list[str]:
    """当前所有**可选**的世界书来源标识（供界面渲染挂载清单）。

    含全局书与角色内嵌书——两者都是「一本可挂载的书」，不再分待遇。
    排序稳定（便于界面展示与比对）。
    """
    sources: set[str] = set()
    for snapshot in snapshots:
        for entry in snapshot.entries:
            sources.add(entry.source_id.split("#", 1)[0] or entry.source)
    return sorted(s for s in sources if s)


def candidate_scopes(snapshots: list[DataSourceSnapshot]) -> set[str]:
    """所有可能持有酒馆来源知识的作用域 + 需要顺带清理的**旧格式**作用域。

    新格式：每本世界书一个 `tavern:book:{哈希}`（由本次读到的快照算出）。
    旧格式（必须一并带上，否则清理不掉）：
    - `tavern`：旧版全局书混放处；
    - `tavern:{persona_id}`：旧版角色内嵌书挂靠处；
    - `*`：**更早**版本曾把全局书写进共享作用域。

    后三者已不再被写入，但盘上可能还留着；不带上它们，旧的全局书会一直
    躺在库里被误召回——这也正是「旧数据清掉重同步」能生效的前提。
    """
    scopes = {SCOPE_ALL, LEGACY_TAVERN_SCOPE}
    for snapshot in snapshots:
        # 新格式：每本世界书一个独立作用域
        for entry in snapshot.entries:
            source_file = entry.source_id.split("#", 1)[0] or entry.source
            scopes.add(book_scope(source_file))
        # 旧格式：内嵌书按角色挂靠的那些键
        for persona_id in scopes_by_character_name(snapshot).values():
            scopes.add(f"{LEGACY_TAVERN_SCOPE}{_LEGACY_SEP}{persona_id}")
    return scopes


def discover_tavern_scopes(
    store: KnowledgeStore,
    snapshots: list[DataSourceSnapshot],
    *,
    mounted: list[str] | None = None,
) -> set[str]:
    """候选作用域 ∪ **真的还有数据**的酒馆作用域（清理逻辑的唯一入口）。

    为什么必须问存储「实际有哪些作用域」：`candidate_scopes` 只从**当前快照**
    推导键。某本书在酒馆里被删掉后，它的 `tavern:book:{哈希}` 已不在候选里，
    那本书的旧文档就永远清不掉——而用户看到的现象正是「删了还在答」。

    三条线索合起来才能不漏：

    1. 当前快照算出的键（`candidate_scopes`）——覆盖「还在的书」与旧格式遗留键；
    2. **挂载清单**——书刚从酒馆删掉、用户还没重新挂载时，清单里仍留着它，
       这条线索让它的残留当场就被清掉；
    3. 存储里实际存在、且像酒馆来源的键——分两类找出：
       · `tavern:book:*`：一次前缀扫描即可（新格式）；
       · 旧格式 `tavern` / `tavern:{persona_id}`：按 `is_legacy_tavern_scope` 判定。

    存储不支持枚举（自定义实现未覆写 `list_scopes`）时，退化为只有线索 1、2——
    行为与改动前一致，不会更糟。
    """
    scopes = set(candidate_scopes(snapshots))
    if mounted:
        scopes.update(book_scope(source) for source in mounted)
    scopes.update(discover_stored_tavern_scopes(store))
    return scopes


def discover_stored_tavern_scopes(store: KnowledgeStore, *, limit: int = 500) -> set[str]:
    """存储里当前真的有数据的**酒馆来源**作用域（新格式 + 旧格式）。"""
    found: set[str] = set()

    # 新格式：`tavern:book:{哈希}` —— 前缀一次挑出（Qdrant 侧下推到 payload 的 scope 字段）
    try:
        found.update(store.list_scopes(prefix=_BOOK_SCOPE_PREFIX, limit=limit))
    except Exception as exc:  # noqa: BLE001 - 枚举失败不该让整批同步失败
        logger.warning("枚举酒馆世界书作用域失败（已跳过）：%s", exc)

    # 旧格式：`tavern` / `tavern:{persona_id}` —— 谓词在存储侧判，正文不出库
    try:
        found.update(
            store.iter_scopes(
                prefix=LEGACY_TAVERN_SCOPE,
                predicate=is_legacy_tavern_scope,
                limit=limit,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 同上
        logger.warning("枚举旧版酒馆作用域失败（已跳过）：%s", exc)

    return found


def _safe_load_mounted() -> list[str]:
    """读全局挂载清单；失败降级为空（清理范围退回「快照 + 存储」两条线索）。

    方向刻意选「空」：读不到清单只是少清几本，而**不会**误删——清理只删
    `source_type == tavern-worldbook` 且当前快照里不再产出的文档。
    """
    try:
        from app.memory.knowledge.mounted import load_mounted_books  # noqa: PLC0415

        return load_mounted_books()
    except Exception as exc:  # noqa: BLE001 - 清单不可用不该阻断同步
        logger.warning("读取挂载清单失败（清理范围退回快照 + 存储）：%s", exc)
        return []


def existing_doc_ids(store: KnowledgeStore, scope: str) -> set[str]:
    """某作用域下**由酒馆世界书写入**的文档 id（不碰用户自己上传的语料）。"""
    return {
        doc.doc_id
        for doc in store.list_documents(scope)
        if doc.source_type == SOURCE_TYPE
    }


def sync_worldbook_knowledge(
    store: KnowledgeStore,
    *,
    snapshots: list[DataSourceSnapshot] | None = None,
    prune: bool = True,
    mounted: list[str] | None = None,
) -> KnowledgeSyncResult:
    """把世界书条目同步进个人知识库（覆盖写，幂等）。

    参数:
        snapshots: 已读到的数据源快照；缺省自行读取（便于调用方复用同一次读取）；
        prune: 是否清理「来源已不存在」的旧文档。默认清理——在酒馆里删掉的世界书
            不该继续被召回，否则用户会遇到「删了还在答」。清理范围不止当前快照
            算出的键，还包括**存储里实际存在**的酒馆作用域（见
            `discover_tavern_scopes`）：否则书一删，它的作用域就不在候选里了。
        mounted: 当前挂载清单（`world/<文件名>` / `char/<角色名>`）。只用来**扩大
            清理范围**（刚删掉的书可能还挂在清单上），不影响写入——写什么由快照决定。
            缺省自行读取全局清单。
    """
    if snapshots is None:
        snapshots, read_warnings = read_snapshots()
    else:
        read_warnings = []

    result = KnowledgeSyncResult(warnings=list(read_warnings))
    groups: dict[tuple[str, str], list[DataSourceEntry]] = {}
    for snapshot in snapshots:
        grouped, orphaned = group_worldbook_entries(snapshot)
        result.skipped += orphaned
        for key, entries in grouped.items():
            groups.setdefault(key, []).extend(entries)

    if result.skipped:
        result.warnings.append(
            f"{result.skipped} 条角色内嵌设定找不到所属角色卡，未写入知识库"
        )

    # 清理：先记下各作用域现有的「酒馆来源」文档，写完再删掉没被覆盖的那些
    stale: dict[str, set[str]] = {}
    if prune:
        if mounted is None:
            mounted = _safe_load_mounted()
        stale = {
            scope: existing_doc_ids(store, scope)
            for scope in discover_tavern_scopes(store, snapshots, mounted=mounted)
        }

    written: set[tuple[str, str]] = set()
    for (scope, source_file), entries in sorted(groups.items()):
        chunks = [_chunk_text(entry) for entry in entries]
        try:
            store.add_document(
                scope,
                title=_doc_title(source_file, len(chunks)),
                chunks=chunks,
                source_type=SOURCE_TYPE,
                doc_id=_doc_id(scope, source_file),  # 同 id = 覆盖
            )
        except Exception as exc:  # noqa: BLE001 - 单篇失败不该让整批同步失败
            result.warnings.append(f"写入失败：{source_file}（{exc}）")
            continue
        result.documents += 1
        result.chunks += len(chunks)
        result.scopes[scope] = result.scopes.get(scope, 0) + 1
        written.add((scope, source_file))

    for scope, doc_ids in stale.items():
        alive = {_doc_id(s, f) for (s, f) in written if s == scope}
        for doc_id in sorted(doc_ids - alive):
            try:
                store.delete_document(scope, doc_id)
            except Exception as exc:  # noqa: BLE001 - 清理失败只记警告
                result.warnings.append(f"清理旧文档失败：{doc_id}（{exc}）")
                continue
            result.removed += 1

    logger.info(
        "世界书 → 知识库同步完成：文档 %d 篇 / 分块 %d / 清理 %d / 作用域 %d",
        result.documents,
        result.chunks,
        result.removed,
        len(result.scopes),
    )
    return result


def worldbook_knowledge_status(
    store: KnowledgeStore, *, snapshots: list[DataSourceSnapshot] | None = None
) -> dict:
    """已入库的酒馆世界书文档统计（供界面显示「同步了没有」）。

    要遍历候选作用域（全局 + 每张角色卡）**以及存储里实际存在的酒馆作用域**：
    只看候选会漏掉「书已从酒馆删掉、但文档还留着」的那些——恰好是用户最想
    在界面上看见的情况。
    """
    if snapshots is None:
        snapshots, _ = read_snapshots()
    docs = [
        doc
        for scope in sorted(discover_tavern_scopes(store, snapshots))
        for doc in store.list_documents(scope)
        if doc.source_type == SOURCE_TYPE
    ]
    return {
        "documents": len(docs),
        "chunks": sum(doc.chunk_count for doc in docs),
        "scopes": len({doc.companion_id for doc in docs}),
    }
