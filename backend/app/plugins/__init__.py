"""插件体系（宿主框架）。

设计见 `AGENTS.md §9`。三层插件（core / builtin / third-party）经**同一注册表**
暴露能力；能力面（provider / tool / datasource / prompt / hook / settings）描述"贡献什么"。

典型用法：

    from app.plugins import PluginRegistry, PluginRegistration, PluginManifest
    from app.plugins.builtin import register_all_builtin

    registry = PluginRegistry()
    register_all_builtin(registry)          # 收编既有 7 个扩展点
    registry.provider("llm", "mock")        # 按能力取实现
"""

from app.plugins.capabilities import (
    CapabilityType,
    Permission,
    PluginLayer,
    PluginState,
)
from app.plugins.context import PluginContext
from app.plugins.manifest import PluginManifest, load_manifest, sort_manifests
from app.plugins.registry import (
    PluginRegistration,
    PluginRegistry,
    get_registry,
    set_registry,
)

__all__ = [
    "CapabilityType",
    "Permission",
    "PluginContext",
    "PluginLayer",
    "PluginManifest",
    "PluginRegistration",
    "PluginRegistry",
    "PluginState",
    "get_registry",
    "load_manifest",
    "set_registry",
    "sort_manifests",
]
