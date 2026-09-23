"""内置插件收编测试：既有 7 个扩展点被正确包装且行为不变。"""

from app.plugins.capabilities import CapabilityType, PluginLayer
from app.plugins.manifest import PluginManifest
from app.plugins.registry import PluginRegistry
from app.plugins.builtin import build_builtin_registrations, register_all_builtin

EXPECTED_IDS = [
    "llm-providers",
    "avatar-providers",
    "memory-warm",
    "memory-knowledge",
    "session-store",
    "tools-builtin",
    "mcp-bridge",
]


def test_all_seven_points_are_registered() -> None:
    registry = PluginRegistry()
    ids = register_all_builtin(registry)
    assert ids == EXPECTED_IDS
    assert len(registry) == 7


def test_all_builtin_are_builtin_layer() -> None:
    """收编的既有实现属于 builtin 层（可禁用/替换），不是 core。"""
    for registration in build_builtin_registrations():
        assert registration.manifest.layer is PluginLayer.BUILTIN
        assert registration.manifest.is_core is False


def test_manifests_have_display_name_and_category() -> None:
    """管理 UI 依赖展示名与分类，二者不可为空。"""
    for registration in build_builtin_registrations():
        manifest = registration.manifest
        assert isinstance(manifest, PluginManifest)
        assert manifest.display_name.strip()
        assert manifest.category.strip()


def test_llm_provider_via_registry() -> None:
    """经注册表取 LLM 实现——行为与直接调用工厂一致。"""
    registry = PluginRegistry()
    register_all_builtin(registry)

    provider = registry.provider("llm", "mock")
    assert provider.name == "mock"

    # 未知实现名仍由原工厂报错（错误信息不丢）
    try:
        registry.provider("llm", "not-a-provider")
    except ValueError as exc:
        assert "未知 LLM provider" in str(exc)
    else:  # pragma: no cover - 防御性
        raise AssertionError("未知 provider 应抛出 ValueError")


def test_session_store_adapter_aligns_signature(tmp_path) -> None:
    """session 工厂的 db_path 是位置参数，适配后仍可正常创建。"""
    registry = PluginRegistry()
    register_all_builtin(registry)

    store = registry.provider("session_store", "memory")
    assert store is not None

    db = tmp_path / "sessions.db"
    sqlite_store = registry.provider("session_store", "sqlite", db_path=str(db))
    assert sqlite_store is not None


def test_tools_builtin_contributes_four_tools() -> None:
    registry = PluginRegistry()
    register_all_builtin(registry)

    names = sorted(spec.name for spec in registry.tools())
    assert names == [
        "query_mood_trend",
        "recall_memory",
        "record_mood_journal",
        "start_breathing_exercise",
    ]


def test_datasource_capability_not_yet_provided() -> None:
    """`datasource` 能力留给 tavern-bridge（P3）；此刻应无提供者。"""
    registry = PluginRegistry()
    register_all_builtin(registry)

    assert "datasource" not in registry.capabilities_summary()
    assert CapabilityType.DATASOURCE.value == "datasource"


def test_disabling_builtin_removes_its_tools() -> None:
    """可禁用性验证：禁用工具插件后，聚合工具列表为空。"""
    registry = PluginRegistry()
    register_all_builtin(registry)
    assert len(registry.tools()) == 4

    registry.set_enabled("tools-builtin", False)
    assert registry.tools() == []
