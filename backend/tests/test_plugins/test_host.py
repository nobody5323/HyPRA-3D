"""插件体系与宿主的集成测试：静态注册、/health 暴露、生命周期不阻断启动。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.plugins.builtin import register_all_builtin
from app.plugins.capabilities import PluginLayer, PluginState
from app.plugins.manager import PluginManager, set_plugin_manager
from app.plugins.manifest import PluginManifest
from app.plugins.registry import PluginRegistration, PluginRegistry, get_registry

client = TestClient(app)


# ---------------- 静态注册（create_app 时完成）----------------


def test_app_registers_builtin_plugins() -> None:
    """导入 app.main 即完成内置插件注册（不依赖 lifespan）。"""
    registry = get_registry()
    ids = [r.manifest.id for r in registry.all()]
    assert "llm-providers" in ids
    assert "tools-builtin" in ids
    assert len(ids) >= 7


def test_registry_has_all_capabilities() -> None:
    summary = get_registry().capabilities_summary()
    for capability in ("llm", "digital_human", "warm_store", "knowledge_store",
                       "session_store", "mcp_manager"):
        assert capability in summary, f"缺少能力：{capability}"


# ---------------- /health 暴露 ----------------


def test_health_exposes_plugins() -> None:
    """健康检查附带插件视图——前端能力中心据此渲染。"""
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert isinstance(body["plugins"], list)
    assert body["plugins"], "内置插件应出现在 /health"

    first = body["plugins"][0]
    for key in ("id", "display_name", "layer", "state", "capabilities", "read_only"):
        assert key in first

    summary = body["plugins_summary"]
    assert summary["total"] == len(body["plugins"])
    assert set(summary["by_layer"]) == {"core", "builtin", "third-party"}


def test_health_keeps_existing_fields() -> None:
    """/health 只能新增字段，不得破坏既有契约（桌面端轮询依赖它）。"""
    body = client.get("/health").json()
    assert body["app"] == "HyPRA Backend"
    assert "mcp" in body


# ---------------- 生命周期 ----------------


def test_manager_setup_and_start_marks_states(tmp_path) -> None:
    """setup → start 推进状态；core/builtin 均进入 STARTED。"""
    registry = PluginRegistry()
    register_all_builtin(registry)
    manager = PluginManager(registry, data_dir=tmp_path, plugin_dirs=[])

    ready = manager.setup_all()
    started = manager.start_all()

    assert set(started) == set(ready)
    assert all(
        manager.registry.get(pid).state is PluginState.STARTED for pid in started
    )

    manager.shutdown_all()
    assert all(
        manager.registry.get(pid).state is PluginState.DISCOVERED for pid in started
    )


def test_broken_plugin_does_not_break_setup(tmp_path) -> None:
    """一个插件 setup 抛异常，不影响其余插件（故障隔离）。"""
    from app.plugins.manifest import PluginManifest
    from app.plugins.registry import PluginRegistration

    def _boom() -> None:
        raise RuntimeError("插件自身故障")

    registry = PluginRegistry()
    register_all_builtin(registry)
    registry.register(
        PluginRegistration(
            manifest=PluginManifest(id="broken", display_name="故障插件"),
            startup=_boom,
        )
    )

    manager = PluginManager(registry, data_dir=tmp_path, plugin_dirs=[])
    ready = manager.setup_all()

    assert "llm-providers" in ready          # 其它插件照常就绪
    assert "broken" not in ready
    broken = registry.get("broken")
    assert broken is not None
    assert broken.state is PluginState.FAILED
    assert "插件自身故障" in broken.error


def test_discover_registers_third_party_from_directory(tmp_path) -> None:
    """第三方插件目录被扫描并注册（manifest 驱动，不执行插件代码）。"""
    plugin_dir = tmp_path / "demo-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        '{"id": "demo-plugin", "display_name": "演示", "layer": "third-party"}',
        encoding="utf-8",
    )

    registry = PluginRegistry()
    manager = PluginManager(registry, data_dir=tmp_path, plugin_dirs=[tmp_path])
    found = manager.discover()

    assert [r.manifest.id for r in found] == ["demo-plugin"]
    assert registry.get("demo-plugin") is not None
    assert registry.get("demo-plugin").state is PluginState.DISCOVERED


def test_discover_missing_entry_marks_failed(tmp_path) -> None:
    """声明了 entry 但文件不存在 → 标记 FAILED，不阻断宿主。"""
    plugin_dir = tmp_path / "bad-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        '{"id": "bad-plugin", "display_name": "坏插件", "entry": "plugin.py"}',
        encoding="utf-8",
    )

    registry = PluginRegistry()
    manager = PluginManager(registry, data_dir=tmp_path, plugin_dirs=[tmp_path])
    manager.discover()

    registration = registry.get("bad-plugin")
    assert registration is not None
    assert registration.state is PluginState.FAILED
    assert "入口文件不存在" in registration.error


def test_discover_builtin_takes_precedence_over_directory(tmp_path) -> None:
    """同 id 时内置注册优先（避免目录里的同名插件顶替内置实现）。"""
    plugin_dir = tmp_path / "llm-providers"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        '{"id": "llm-providers", "display_name": "伪造的内置插件"}',
        encoding="utf-8",
    )

    registry = PluginRegistry()
    register_all_builtin(registry)
    before = registry.get("llm-providers").manifest.display_name

    manager = PluginManager(registry, data_dir=tmp_path, plugin_dirs=[tmp_path])
    manager.discover()

    assert registry.get("llm-providers").manifest.display_name == before == "对话模型接入"


def test_set_plugin_manager_replaces_singleton(tmp_path) -> None:
    """全局单例可被替换（测试隔离用），用完还原。"""
    from app.plugins.manager import get_plugin_manager

    original = get_plugin_manager()
    try:
        registry = PluginRegistry()
        replacement = PluginManager(registry, data_dir=tmp_path, plugin_dirs=[])
        set_plugin_manager(replacement)
        assert get_plugin_manager() is replacement
        assert get_plugin_manager().status() == []
    finally:
        set_plugin_manager(original)


# ---------------- 启用状态持久化（重启后开关不回弹）----------------
#
# 注册表的 _enabled_overrides 是运行期值；不落盘则重启回弹到 manifest 默认值，
# 对 enabled:false 的插件（如 tavern-bridge）表现为「点启用 → 重启又没了」。


def _registry_with_builtin() -> PluginRegistry:
    registry = PluginRegistry()
    register_all_builtin(registry)
    return registry


def _make_manager(tmp_path) -> PluginManager:
    """只有内置插件、数据目录在 tmp_path 的 manager。"""
    return PluginManager(_registry_with_builtin(), data_dir=tmp_path, plugin_dirs=[])


def _registry_with_core() -> tuple[PluginRegistry, str]:
    """内置插件 + 一个 core 层插件。

    core 目前是分层概念（AGENTS.md §9.10），builtin.py 尚未把 core 能力注册为插件，
    因此测试里自建一个以覆盖「core 不可禁用」这条硬约束。
    """
    registry = PluginRegistry()
    register_all_builtin(registry)
    registry.register(
        PluginRegistration(
            manifest=PluginManifest(
                id="core-demo", display_name="核心演示", layer=PluginLayer.CORE
            )
        )
    )
    return registry, "core-demo"


def _enabled_ids(manager: PluginManager) -> list[str]:
    return [r.manifest.id for r in manager.registry.enabled()]


def _state_of(manager: PluginManager, plugin_id: str) -> str:
    return next(item["state"] for item in manager.status() if item["id"] == plugin_id)


def test_set_enabled_writes_state_file(tmp_path) -> None:
    manager = _make_manager(tmp_path)
    manager.set_enabled("tools-builtin", False)

    state_path = tmp_path / "plugins" / "tools-builtin" / "state.json"
    assert state_path.is_file()
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"enabled": False}


def test_disabled_plugin_is_not_reported_as_running(tmp_path) -> None:
    """禁用后 status() 必须报 disabled——不能一边 enabled=false 一边显示「运行中」。"""
    manager = _make_manager(tmp_path)
    manager.setup_all()
    manager.start_all()
    assert _state_of(manager, "tools-builtin") == "started"

    manager.set_enabled("tools-builtin", False)

    assert _state_of(manager, "tools-builtin") == "disabled"
    assert manager.registry.get("tools-builtin").state is PluginState.DISCOVERED
    assert "tools-builtin" not in _enabled_ids(manager)


def test_disabled_plugin_can_be_reenabled_in_same_run(tmp_path) -> None:
    """禁用后再启用，无需重启即可恢复 STARTED。"""
    manager = _make_manager(tmp_path)
    manager.set_enabled("tools-builtin", False)
    manager.set_enabled("tools-builtin", True)

    assert _state_of(manager, "tools-builtin") == "started"
    assert "tools-builtin" in _enabled_ids(manager)


def test_reload_all_keeps_plugins_started(tmp_path) -> None:
    """保存配置后插件仍为 STARTED——只 setup 不 start 会退成 LOADED，界面像能力掉了。"""
    manager = _make_manager(tmp_path)
    manager.reload_all()
    assert _state_of(manager, "tools-builtin") == "started"

    manager.reload_all()
    assert _state_of(manager, "tools-builtin") == "started"
    assert "tools-builtin" in _enabled_ids(manager)


def test_load_enabled_states_restores_on_discover(tmp_path) -> None:
    """落盘的禁用状态在下次启动（discover）时恢复。"""
    state_dir = tmp_path / "plugins" / "tools-builtin"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text('{"enabled": false}', encoding="utf-8")

    manager = _make_manager(tmp_path)
    manager.discover()  # 启动路径

    assert _state_of(manager, "tools-builtin") == "disabled"
    assert "tools-builtin" not in _enabled_ids(manager)


def test_core_plugin_state_file_is_ignored(tmp_path) -> None:
    """core 层恒启用：磁盘上留着 enabled=false 也不读（AGENTS.md §9.2）。"""
    registry, core_id = _registry_with_core()
    state_dir = tmp_path / "plugins" / core_id
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text('{"enabled": false}', encoding="utf-8")

    manager = PluginManager(registry, data_dir=tmp_path, plugin_dirs=[])

    assert core_id not in manager.load_enabled_states()
    assert core_id in _enabled_ids(manager)


def test_core_plugin_cannot_be_disabled_and_writes_no_state(tmp_path) -> None:
    """core 层禁用被拒，且不留自相矛盾的状态文件。"""
    registry, core_id = _registry_with_core()
    manager = PluginManager(registry, data_dir=tmp_path, plugin_dirs=[])

    with pytest.raises(ValueError):
        manager.set_enabled(core_id, False)

    assert not (tmp_path / "plugins" / core_id / "state.json").exists()


def test_corrupt_state_file_falls_back_to_manifest_default(tmp_path) -> None:
    """状态文件损坏不该阻断启动，回落 manifest 默认值。"""
    state_dir = tmp_path / "plugins" / "tools-builtin"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text("{ 不是 JSON", encoding="utf-8")

    manager = _make_manager(tmp_path)

    assert manager.load_enabled_states() == {}
    assert "tools-builtin" in _enabled_ids(manager)


def test_state_file_requires_real_bool(tmp_path) -> None:
    """手改成字符串 "false" 不生效——只有真正的 bool 才算状态。"""
    state_dir = tmp_path / "plugins" / "tools-builtin"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text('{"enabled": "false"}', encoding="utf-8")

    manager = _make_manager(tmp_path)

    assert manager.load_enabled_states() == {}
    assert "tools-builtin" in _enabled_ids(manager)
