"""注册表测试：三层注册、能力取用、启停、权限强制、聚合视图。"""

import pytest

from app.plugins.capabilities import (
    CapabilityType,
    FilesystemPermission,
    Permission,
    PluginLayer,
)
from app.plugins.manifest import PluginManifest
from app.plugins.registry import PluginRegistration, PluginRegistry


def _reg(
    plugin_id: str,
    *,
    layer: PluginLayer = PluginLayer.BUILTIN,
    order: int = 100,
    providers: dict | None = None,
    tools: list | None = None,
    capabilities: list[CapabilityType] | None = None,
    permissions: Permission | None = None,
    hooks: dict | None = None,
) -> PluginRegistration:
    return PluginRegistration(
        manifest=PluginManifest(
            id=plugin_id,
            display_name=plugin_id,
            layer=layer,
            loading_order=order,
            capabilities=capabilities or [CapabilityType.PROVIDER],
            permissions=permissions or Permission(),
        ),
        providers=providers or {},
        tools=tools or [],
        hooks=hooks or {},
    )


# ---------------- 注册与查询 ----------------


def test_register_and_get() -> None:
    registry = PluginRegistry()
    registry.register(_reg("demo"))
    assert registry.get("demo") is not None
    assert len(registry) == 1
    assert registry.get("missing") is None


def test_duplicate_registration_overwrites() -> None:
    """同 id 重复注册以最后一次为准（便于测试替换实现）。"""
    registry = PluginRegistry()
    registry.register(_reg("demo", providers={"llm": lambda name, **kw: "first"}))
    registry.register(_reg("demo", providers={"llm": lambda name, **kw: "second"}))
    assert len(registry) == 1
    assert registry.provider("llm") == "second"


def test_all_sorted_by_layer_then_order() -> None:
    """展示顺序：core → builtin → third-party，层内按 loading_order。"""
    registry = PluginRegistry()
    registry.register(_reg("builtin-late", order=200))
    registry.register(_reg("core-a", layer=PluginLayer.CORE, order=5))
    registry.register(_reg("third", layer=PluginLayer.THIRD_PARTY, order=1))
    registry.register(_reg("builtin-early", order=10))

    assert [r.manifest.id for r in registry.all()] == [
        "core-a",
        "builtin-early",
        "builtin-late",
        "third",
    ]


# ---------------- 权限强制 ----------------


def test_readonly_plugin_cannot_declare_write_paths() -> None:
    """只读插件携带写路径 → 注册即被拒绝（架构约束，非口头承诺）。"""
    registry = PluginRegistry()
    with pytest.raises(ValueError, match="filesystem.write=false"):
        registry.register(
            _reg(
                "bad",
                permissions=Permission(
                    filesystem=FilesystemPermission(write=False, write_paths=["/tmp/x"])
                ),
            )
        )


def test_writable_plugin_accepts_write_paths() -> None:
    registry = PluginRegistry()
    registry.register(
        _reg(
            "ok",
            permissions=Permission(
                filesystem=FilesystemPermission(write=True, write_paths=["/tmp/x"])
            ),
        )
    )
    assert registry.get("ok") is not None


# ---------------- 启停 ----------------


def test_core_plugin_cannot_be_disabled() -> None:
    registry = PluginRegistry()
    registry.register(_reg("core-x", layer=PluginLayer.CORE))
    with pytest.raises(ValueError, match="core"):
        registry.set_enabled("core-x", False)
    assert [r.manifest.id for r in registry.enabled()] == ["core-x"]


def test_disabled_builtin_is_excluded() -> None:
    registry = PluginRegistry()
    registry.register(_reg("builtin-x"))
    registry.set_enabled("builtin-x", False)
    assert registry.enabled() == []


def test_set_enabled_unknown_plugin() -> None:
    with pytest.raises(KeyError):
        PluginRegistry().set_enabled("nope", False)


# ---------------- 能力取用 ----------------


def test_provider_passes_implementation_name_to_factory() -> None:
    """实现名透传给工厂——与既有 factory(第一参数是实现名) 的签名对齐。"""
    seen: dict = {}

    def factory(name: str, **kwargs):
        seen["name"] = name
        seen["kwargs"] = kwargs
        return f"impl:{name}"

    registry = PluginRegistry()
    registry.register(_reg("p", providers={"llm": factory}))

    assert registry.provider("llm", "qdrant", api_key="k") == "impl:qdrant"
    assert seen == {"name": "qdrant", "kwargs": {"api_key": "k"}}


def test_provider_first_enabled_plugin_wins() -> None:
    """多个插件提供同一能力时，按 loading_order 取第一个启用的。"""
    registry = PluginRegistry()
    registry.register(_reg("late", order=200, providers={"warm_store": lambda n, **k: "late"}))
    registry.register(_reg("early", order=10, providers={"warm_store": lambda n, **k: "early"}))
    assert registry.provider("warm_store") == "early"

    registry.set_enabled("early", False)
    assert registry.provider("warm_store") == "late"


def test_provider_unknown_capability() -> None:
    with pytest.raises(KeyError, match="没有启用"):
        PluginRegistry().provider("nonexistent")


def test_provider_respects_disabled() -> None:
    registry = PluginRegistry()
    registry.register(_reg("only", providers={"llm": lambda n, **k: "x"}))
    registry.set_enabled("only", False)
    with pytest.raises(KeyError):
        registry.provider("llm")


# ---------------- 聚合视图 ----------------


def test_tools_and_hooks_aggregation() -> None:
    registry = PluginRegistry()
    registry.register(_reg("t1", order=10, tools=["tool-a"]))
    registry.register(_reg("t2", order=20, tools=["tool-b"], hooks={"after_reply": "h2"}))
    registry.register(_reg("t3", order=30, tools=["tool-c"], hooks={"after_reply": "h3"}))

    assert registry.tools() == ["tool-a", "tool-b", "tool-c"]
    # hook 按插件 loading_order 排序
    assert registry.hook_handlers("after_reply") == ["h2", "h3"]
    assert registry.hook_handlers("unknown") == []


def test_disabled_plugin_contributes_nothing() -> None:
    registry = PluginRegistry()
    registry.register(_reg("t", tools=["t"], hooks={"e": "h"}))
    registry.set_enabled("t", False)
    assert registry.tools() == []
    assert registry.hook_handlers("e") == []


def test_capabilities_summary() -> None:
    registry = PluginRegistry()
    registry.register(_reg("a", providers={"llm": lambda n, **k: None}))
    registry.register(_reg("b", providers={"warm_store": lambda n, **k: None}))
    assert registry.capabilities_summary() == {"llm": ["a"], "warm_store": ["b"]}


# ---------------- 全局单例 ----------------


def test_global_registry_singleton() -> None:
    from app.plugins.registry import get_registry, set_registry

    set_registry(None)
    first = get_registry()
    assert get_registry() is first
    set_registry(None)   # 清理，避免影响其它用例
