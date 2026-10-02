"""插件体系（宿主框架）。

设计见 `AGENTS.md §9`。三层插件（core / builtin / third-party）经**同一注册表**
暴露能力；能力面（provider / tool / datasource / prompt / hook / settings）描述"贡献什么"。

**写插件的入口是 `app.plugins.sdk`**（公开接口面），不是本模块：

    from app.plugins.sdk import PluginContext, ToolSpec, DataSourceSnapshot

    def build(ctx: PluginContext) -> dict:
        return {"tools": [...]}

接口说明见 `docs/plugin-development.md`，可复制的最小示例见
`docs/examples/plugin-hello/`。本模块（`__init__`）是**宿主侧**的聚合导出：

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
