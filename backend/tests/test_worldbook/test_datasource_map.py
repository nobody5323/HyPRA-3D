"""数据源 → 世界书条目的宿主侧映射测试。

夹具全部**自创**（`AGENTS.md §8.6` 红线）：不使用任何真实酒馆世界书内容，
只复刻它们的**结构**（常驻条目、深度注入、角色内嵌书、outlet 档、概率字段…）。
"""

from app.plugins.contracts import DataSourceCharacter, DataSourceEntry, DataSourceSnapshot
from app.prompts.persona.datasource_map import persona_id_for
from app.worldbook.datasource_map import (
    collect_worldbook_entries,
    entries_from_snapshot,
    entry_id,
)
from app.worldbook.models import SCOPE_ALL


def _raw(**overrides) -> DataSourceEntry:
    base = {
        "title": "示例条目",
        "content": "示例正文",
        "keys": ("茶馆",),
        "source": "demo-source",
        "source_id": "book#1",
    }
    base.update(overrides)
    return DataSourceEntry(**base)


def _snapshot(*entries) -> DataSourceSnapshot:
    return DataSourceSnapshot(entries=list(entries))


# =============================================================
# 字段映射
# =============================================================


def test_fields_map_one_to_one() -> None:
    """中性契约的字段一一落到宿主模型上，不做解释性改写。"""
    raw = _raw(
        keys=("茶馆", "茶叶"),
        constant=True,
        position="at_depth",
        depth=2,
        order=30,
        case_sensitive=True,
        enabled=False,
    )

    entries, warnings = entries_from_snapshot(_snapshot(raw))

    assert warnings == []
    (entry,) = entries
    assert entry.title == "示例条目"
    assert entry.content == "示例正文"
    assert entry.keys == ["茶馆", "茶叶"]
    assert entry.constant is True
    assert (entry.position, entry.depth) == ("at_depth", 2)
    assert entry.order == 30
    assert entry.case_sensitive is True
    assert entry.enabled is False


def test_scope_is_always_global() -> None:
    """全局世界书一律归 `*`：本次只接 `worlds/*.json` 这类全局书。"""
    (entry,), _ = entries_from_snapshot(_snapshot(_raw()))
    assert entry.scope == SCOPE_ALL


def test_priority_stays_below_builtin() -> None:
    """外部导入的设定不该悄悄改掉本项目的注入次序：priority 留默认 0。

    内置条目是 priority=10（见 app/worldbook/entries/*.yaml），因此外来条目
    在同分时排在后面；组内次序由来源自带的 order 决定。
    """
    (entry,), _ = entries_from_snapshot(_snapshot(_raw()))
    assert entry.priority == 0


def test_constant_entry_becomes_injectable() -> None:
    """常驻 + 无关键词的来源条目，映射后仍应具备触发条件（＝恒命中）。"""
    (entry,), _ = entries_from_snapshot(_snapshot(_raw(keys=(), constant=True)))
    assert entry.constant is True
    assert entry.has_trigger is True


def test_at_depth_fields_are_passed_through() -> None:
    """`position` / `depth` / `role` 三个档位字段一一映射（它们是分档注入的全部依据）。"""
    raw = _raw(position="at_depth", depth=2, role="user")

    (entry,), _ = entries_from_snapshot(_snapshot(raw))

    assert (entry.position, entry.depth, entry.role) == ("at_depth", 2, "user")


# =============================================================
# id 稳定性
# =============================================================


def test_ids_are_stable_and_distinct() -> None:
    """同一来源条目重读得到同一 id；不同来源条目得到不同 id。"""
    assert entry_id("s", "a") == entry_id("s", "a")
    assert entry_id("s", "a") != entry_id("s", "b")
    assert entry_id("s", "a") != entry_id("t", "a")


def test_id_is_safe_for_host_use() -> None:
    """来源 id 里的中文 / `/` / `#` 不得泄漏进宿主 id（它会被当键用）。"""
    generated = entry_id("world/世界甲", "世界甲#1")
    assert generated.startswith("tb-")
    assert generated.isascii()
    assert "/" not in generated and "#" not in generated


# =============================================================
# 跳过与降级（都必须在警告里说出来）
# =============================================================


def test_character_embedded_entries_get_the_character_scope() -> None:
    """★ 角色内嵌世界书归到**该角色卡对应的 persona**（两边用同一个 id 函数）。

    归 `*` 会把某个角色的专属设定注入到所有陪伴对象里（串味）；
    归一个对不上的 id，那些条目则永远注入不进去——两种都是错的。
    """
    character = DataSourceCharacter(
        name="示例角色", description="设定正文", source="demo-source", source_id="char#1"
    )
    embedded = _raw(
        source_id="char/示例角色#1",
        extra={"character": "示例角色"},
    )
    snapshot = DataSourceSnapshot(entries=[embedded], characters=[character])

    entries, warnings = entries_from_snapshot(snapshot)

    assert warnings == []
    assert [entry.scope for entry in entries] == [persona_id_for(character)]
    # 与 persona 清单里的 id 是同一个（否则 scope 永远匹配不上）
    assert persona_id_for(character).startswith("tbp-")


def test_character_embedded_entries_are_skipped_when_card_is_missing() -> None:
    """找不到所属角色卡的内嵌书宁可不接：归错人比不注入更糟。"""
    embedded = _raw(source_id="char/示例角色#1", extra={"character": "示例角色"})
    global_entry = _raw(source_id="book#1")

    entries, warnings = entries_from_snapshot(_snapshot(embedded, global_entry))

    assert [entry.title for entry in entries] == ["示例条目"]
    assert len(warnings) == 1
    assert "角色内嵌世界书 1 条未接入" in warnings[0]


def test_empty_content_is_skipped() -> None:
    entries, warnings = entries_from_snapshot(_snapshot(_raw(content="   ")))
    assert entries == []
    assert "正文为空的条目 1 条已跳过" in warnings[0]


def test_outlet_position_is_skipped() -> None:
    """outlet 档在 ST 里是宏出口、不进提示词；宿主没有宏出口，只能跳过。"""
    entries, warnings = entries_from_snapshot(_snapshot(_raw(position="outlet")))
    assert entries == []
    assert any("outlet 档条目 1 条已跳过" in warning for warning in warnings)


def test_probability_is_reported_as_unsupported() -> None:
    """概率通道未实现 → 按必然触发处理，但必须让用户知道。"""
    entries, warnings = entries_from_snapshot(_snapshot(_raw(probability=40)))

    assert len(entries) == 1  # 不丢条目，只是降级
    assert any("触发概率" in warning for warning in warnings)


def test_warnings_are_aggregated_not_per_entry() -> None:
    """警告按类别汇总：几百条逐条刷屏等于没有警告。"""
    raws = [_raw(source_id=f"book#{i}", probability=40) for i in range(50)]
    _, warnings = entries_from_snapshot(_snapshot(*raws))

    assert len(warnings) == 1
    assert "50 条" in warnings[0]


def test_character_cards_are_not_worldbook_entries() -> None:
    """角色卡走 persona 通道，不该被当成世界书条目。"""
    snapshot = DataSourceSnapshot(
        entries=[_raw()],
        characters=[DataSourceCharacter(name="示例角色")],
    )
    entries, _ = entries_from_snapshot(snapshot)
    assert [entry.title for entry in entries] == ["示例条目"]


# =============================================================
# 走插件注册表的聚合通路
# =============================================================


class _FakeDataSource:
    """最小数据源替身：只有 `read()`，产出中性快照。"""

    def __init__(self, *entries: DataSourceEntry) -> None:
        self._snapshot = DataSourceSnapshot(entries=list(entries))

    def read(self) -> DataSourceSnapshot:
        return self._snapshot


class _NotAWorldbookSource:
    """别种 datasource（如数字人模型来源）：没有 `read()`，必须被跳过。"""

    def list_models(self) -> list[str]:
        return []


def _install_sources(tmp_path, *sources):
    """把全局插件管理器换成只含给定数据源的最小实例，返回原管理器（用例负责恢复）。"""
    from app.plugins.manager import PluginManager, get_plugin_manager, set_plugin_manager
    from app.plugins.manifest import PluginManifest
    from app.plugins.registry import PluginRegistration, PluginRegistry

    registry = PluginRegistry()
    registry.register(
        PluginRegistration(
            manifest=PluginManifest(id="demo-source", display_name="示例数据源"),
            datasources=list(sources),
        )
    )
    original = get_plugin_manager()
    set_plugin_manager(PluginManager(registry, data_dir=tmp_path, plugin_dirs=[]))
    return original


def test_collect_walks_plugin_datasources(tmp_path) -> None:
    """聚合走 `PluginRegistry.datasources()`：宿主不写死插件 id。"""
    from app.plugins.manager import set_plugin_manager

    original = _install_sources(tmp_path, _FakeDataSource(_raw()))
    try:
        entries, warnings = collect_worldbook_entries()
    finally:
        set_plugin_manager(original)

    assert [entry.title for entry in entries] == ["示例条目"]
    assert warnings == []


def test_non_worldbook_datasources_are_ignored(tmp_path) -> None:
    """鸭子类型识别：没有 `read()` 的数据源直接跳过，不报错。"""
    from app.plugins.manager import set_plugin_manager

    original = _install_sources(
        tmp_path, _NotAWorldbookSource(), _FakeDataSource(_raw())
    )
    try:
        entries, warnings = collect_worldbook_entries()
    finally:
        set_plugin_manager(original)

    assert [entry.title for entry in entries] == ["示例条目"]
    assert warnings == []


def test_broken_datasource_does_not_break_the_rest(tmp_path) -> None:
    """单个数据源读失败只记警告，其它数据源照常接入（不能因一个坏源丢全部设定）。"""
    from app.plugins.manager import set_plugin_manager

    class _Boom(_FakeDataSource):
        def read(self) -> DataSourceSnapshot:
            raise RuntimeError("故意炸掉")

    original = _install_sources(
        tmp_path, _Boom(), _FakeDataSource(_raw())
    )
    try:
        entries, warnings = collect_worldbook_entries()
    finally:
        set_plugin_manager(original)

    assert [entry.title for entry in entries] == ["示例条目"]
    assert any("读取失败" in warning for warning in warnings)


# =============================================================
# 端到端接线：开关 → 对话用的条目列表
# =============================================================


def test_chat_entries_exclude_tavern_worldbook(tmp_path) -> None:
    """★ 端到端：酒馆世界书不再进入直接 WorldBookEntry 注入列表。

    酒馆世界书仍由数据源映射层读取，并通过知识库同步/检索链路使用；
    `get_worldbook_entries()` 只返回本地内置与用户自建世界书，避免整本酒馆世界书
    被常驻注入或抢占世界书预算。
    """
    from app.api import chat as chat_module
    from app.plugins.manager import set_plugin_manager

    tavern_id = entry_id("demo-source", "book#1")
    original = _install_sources(tmp_path, _FakeDataSource(_raw(keys=(), constant=True)))
    try:
        entries = {entry.id: entry for entry in chat_module.get_worldbook_entries()}
    finally:
        set_plugin_manager(original)

    assert tavern_id not in entries
    # 本地内置条目不受影响
    assert "consulting-room" in entries
