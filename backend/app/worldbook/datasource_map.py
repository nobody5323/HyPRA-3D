"""数据源 → 世界书条目的宿主侧映射（`AGENTS.md §9.4` 的硬约定）。

插件只负责「读 + 产出中性数据」（`DataSourceSnapshot`）；把它变成宿主的
`WorldBookEntry` 是**宿主**的事——这样换数据源（酒馆、别的客户端导出…）只换插件，
映射器与提示词链路一动不动。插件因此也不必知道 `WorldBookEntry` 长什么样。

归属（`scope`）规则：
- **全局世界书**（`worlds/*.json`）一律 `scope="*"`；
- **角色内嵌世界书**归到该角色卡对应的 persona id（`tbp-…`）。两边用同一个
  `persona_id_for()` 算 id，所以「这个角色的专属设定只在这个角色的知识检索中出现」才成立
  （见 `app/prompts/persona/datasource_map.py`）。

`position` / `depth` 字段仍如实保存，供导入状态与兼容层展示；酒馆世界书的新主路径
不再按这些字段直接写入 prompt，而是作为知识库分块参与向量 + BM25 检索。
"""

from __future__ import annotations

from app.plugins.contracts import DataSourceEntry, DataSourceSnapshot
from app.plugins.datasources import read_snapshots, stable_item_id
from app.prompts.persona.datasource_map import scopes_by_character_name
from app.worldbook.models import POSITION_OUTLET, SCOPE_ALL, WorldBookEntry

#: 世界书条目的宿主 id 命名空间（与 persona 的 `tbp-` 分开，两个命名空间各管各的）
WORLDBOOK_ID_PREFIX = "tb"


def entry_id(source: str, source_id: str) -> str:
    """来源条目的稳定宿主 id，见 `app/plugins/datasources.stable_item_id`。"""
    return stable_item_id(WORLDBOOK_ID_PREFIX, source, source_id)


def _map_entry(raw: DataSourceEntry, *, scope: str) -> WorldBookEntry:
    """单条中性条目 → 宿主世界书条目（字段一一对应，不做解释性的改写）。"""
    return WorldBookEntry(
        id=entry_id(raw.source, raw.source_id),
        title=raw.title,
        content=raw.content,
        keys=list(raw.keys),
        # priority 刻意留默认 0：内置条目（priority 10）应当压过外部导入的设定，
        # 外来内容不该悄悄改掉本项目的注入次序。组内次序交给 order。
        constant=raw.constant,
        position=raw.position,
        depth=raw.depth,
        order=raw.order,
        role=raw.role,
        enabled=raw.enabled,
        case_sensitive=raw.case_sensitive,
        scope=scope,
    )


def entries_from_snapshot(
    snapshot: DataSourceSnapshot,
) -> tuple[list[WorldBookEntry], list[str]]:
    """把一次数据源读取的条目映射为世界书条目，返回 (条目, 警告)。

    警告按**类别汇总**而不是逐条报：真实世界书动辄几百条，70 条「档位未分档」
    逐条刷屏等于没有警告。跳过与降级的原因必须让用户看得见（§8.6 的用户知情）。
    """
    out: list[WorldBookEntry] = []
    character_scopes = scopes_by_character_name(snapshot)
    orphaned = empty = outlet = probabilistic = 0

    for raw in snapshot.entries:
        owner = str(raw.extra.get("character") or "")
        if owner:
            # 角色内嵌世界书：归属必须是它所属角色对应的 persona
            scope = character_scopes.get(owner)
            if scope is None:
                orphaned += 1
                continue
        else:
            scope = SCOPE_ALL
        if not raw.content.strip():
            empty += 1
            continue
        # outlet 档在 ST 里是「暴露成宏、不进提示词」的出口，宿主没有宏出口
        if raw.position == POSITION_OUTLET:
            outlet += 1
            continue

        out.append(_map_entry(raw, scope=scope))
        if raw.probability < 100:
            probabilistic += 1

    warnings: list[str] = []
    if orphaned:
        warnings.append(
            f"角色内嵌世界书 {orphaned} 条未接入（找不到它们所属的角色卡）"
        )
    if empty:
        warnings.append(f"正文为空的条目 {empty} 条已跳过")
    if outlet:
        warnings.append(f"outlet 档条目 {outlet} 条已跳过（该档是宏出口，不进提示词）")
    if probabilistic:
        warnings.append(
            f"声明了触发概率的条目 {probabilistic} 条按「必然触发」处理（概率通道未实现）"
        )
    return out, warnings


def collect_worldbook_entries() -> tuple[list[WorldBookEntry], list[str]]:
    """聚合**启用插件**贡献的世界书条目，返回 (条目, 警告)。"""
    snapshots, warnings = read_snapshots()
    entries: list[WorldBookEntry] = []
    for snapshot in snapshots:
        mapped, source_warnings = entries_from_snapshot(snapshot)
        entries.extend(mapped)
        warnings.extend(source_warnings)
    return entries, warnings
