"""世界书 → 个人知识库：把外部设定转成**可按需检索**的语料。

为什么不直接注入上下文：真实世界书动辄几百条（实测 274 条，其中 133 条是常驻型），
而世界书注入预算只有 400 token —— 全塞的结果是大部分被截掉，而且**每一轮都在占位**。
转成知识库后它们是**按需召回**的：问到相关的事才进上下文，不问就不占预算。

分篇规则 —— `(作用域, 来源文件)` 各一篇：

- **全局世界书**（`scope="*"`）→ `tavern` 作用域，只存一份、所有酒馆会话共用；
- **角色内嵌书** → `tavern:{该角色的 persona id}`，只有该角色召回得到（§8.2 的记忆隔离）。

**酒馆来源单独成库、且只在酒馆聊天模式下召回**（AGENTS.md §8.1 / §8.2）：
作用域键一律由 `app/memory/knowledge/scopes` 算出——写入侧与检索侧共用同一套函数，
两边各算各的话，内容就会挂在一个永远匹配不上的键上（表现为「明明导了却召不回」）。

`doc_id` 由来源标识算出而非随机 → 重复同步是**覆盖同一篇**，不会越同步越多；
同时清理「来源已不存在」的旧文档（在酒馆里删了世界书，这里也不该继续留着）。
旧版本曾把全局书写进共享作用域 `*`，清理逻辑会顺手把它们删掉（见 `candidate_scopes`）。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from app.memory.knowledge.base import KnowledgeStore
from app.memory.knowledge.scopes import TAVERN_SCOPE, tavern_scope
from app.plugins.contracts import DataSourceEntry, DataSourceSnapshot
from app.plugins.datasources import read_snapshots, stable_item_id
from app.prompts.persona.datasource_map import scopes_by_character_name
from app.worldbook.models import POSITION_OUTLET, SCOPE_ALL

logger = logging.getLogger(__name__)

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
    #: 每个知识作用域写入了多少篇（全局作用域是 `*`）
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
    作用域一律带 `tavern` 前缀（见模块文档）：酒馆来源只在酒馆聊天模式下召回。
    """
    character_scopes = scopes_by_character_name(snapshot)
    groups: dict[tuple[str, str], list[DataSourceEntry]] = defaultdict(list)
    orphaned = 0

    for entry in snapshot.entries:
        if not entry.content.strip():
            continue
        if entry.position == POSITION_OUTLET:
            continue  # 宏出口档不进提示词，也不该进知识库
        owner = str(entry.extra.get("character") or "")
        if owner:
            persona_id = character_scopes.get(owner)
            if persona_id is None:
                orphaned += 1  # 找不到角色卡：宁可不进库，也不能挂到别人的作用域上
                continue
            scope = tavern_scope(persona_id)
        else:
            scope = TAVERN_SCOPE
        # source_id 形如 `world/世界甲#7` / `char/角色名#3`：去掉条目序号就是来源文件
        source_file = entry.source_id.split("#", 1)[0] or entry.source
        groups[(scope, source_file)].append(entry)
    return dict(groups), orphaned


def _doc_id(scope: str, source_file: str) -> str:
    """文档 id：同来源恒等 → 重复同步覆盖同一篇。"""
    return stable_item_id(DOC_ID_PREFIX, scope, source_file)


def _doc_title(source_file: str, count: int) -> str:
    kind, _, name = source_file.partition("/")
    label = "角色内嵌设定" if kind == "char" else "世界书"
    return f"[酒馆] {label}·{name or source_file}（{count} 条）"


def candidate_scopes(snapshots: list[DataSourceSnapshot]) -> set[str]:
    """所有可能持有酒馆来源知识的作用域：`tavern` 全局 + 每张角色卡的 `tavern:{id}`。

    刻意**带上共享作用域 `SCOPE_ALL`**：旧版本把全局书写在 `*` 下，
    带上它，清理逻辑才能在下次同步时把那些旧文档删掉（否则它们会一直
    留在共享作用域里，继续污染桌宠模式）。
    """
    scopes = {SCOPE_ALL, TAVERN_SCOPE}
    for snapshot in snapshots:
        for persona_id in scopes_by_character_name(snapshot).values():
            scopes.add(tavern_scope(persona_id))
    return scopes


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
) -> KnowledgeSyncResult:
    """把世界书条目同步进个人知识库（覆盖写，幂等）。

    参数:
        snapshots: 已读到的数据源快照；缺省自行读取（便于调用方复用同一次读取）；
        prune: 是否清理「来源已不存在」的旧文档。默认清理——在酒馆里删掉的世界书
            不该继续被召回，否则用户会遇到「删了还在答」。
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
        stale = {
            scope: existing_doc_ids(store, scope)
            for scope in candidate_scopes(snapshots)
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

    要遍历全部候选作用域（全局 + 每张角色卡）：只看全局会漏掉角色专属的那些。
    """
    if snapshots is None:
        snapshots, _ = read_snapshots()
    docs = [
        doc
        for scope in sorted(candidate_scopes(snapshots))
        for doc in store.list_documents(scope)
        if doc.source_type == SOURCE_TYPE
    ]
    return {
        "documents": len(docs),
        "chunks": sum(doc.chunk_count for doc in docs),
        "scopes": len({doc.companion_id for doc in docs}),
    }
