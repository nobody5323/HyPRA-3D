"""插件体系与宿主的集成测试：静态注册、/health 暴露、生命周期不阻断启动。"""

from fastapi.testclient import TestClient

from app.main import app
from app.plugins.builtin import register_all_builtin
from app.plugins.capabilities import PluginState
from app.plugins.manager import PluginManager, set_plugin_manager
from app.plugins.registry import PluginRegistry, get_registry

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
