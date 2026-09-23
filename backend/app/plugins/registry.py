"""插件注册表：三层插件的能力总入口。

设计要点（`AGENTS.md §9.3 / §9.4`）：
- **统一注册**：内置插件与第三方插件都归一化为 `PluginRegistration`；
- **能力索引**：按能力面分门别类，宿主其它层通过本表取用（不再直接 import 具体实现）；
- **权限强制**：注册时校验权限声明，只读插件不可能拿到写句柄。

本模块不依赖任何业务模块——业务层在启动时把实现注册进来（见 `builtin.py`）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.plugins.capabilities import CapabilityType, PluginLayer, PluginState
from app.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

# provider 工厂签名：接受**实现名**与可选参数，返回实现实例
# （既有工厂的第一参数均为实现名，如 create_warm_store("qdrant", ...)）
ProviderFactory = Callable[..., Any]
# hook 处理器：接收事件载荷，返回值被忽略（只读通知）或用于改写（由宿主决定）
HookHandler = Callable[..., Any]


@dataclass
class PluginRegistration:
    """一个插件注册的全部内容。

    插件**只填自己有的部分**：LLM 插件填 `providers`，工具插件填 `tools`，
    酒馆接入插件填 `datasources` + `prompt_fragments` + `tools`。
    """

    manifest: PluginManifest
    providers: dict[str, ProviderFactory] = field(default_factory=dict)
    """providers[能力名] = 工厂；例：{"llm": create_llm_provider, "digital_human": ...}"""

    tools: list[Any] = field(default_factory=list)
    """`ToolSpec` 列表（`app.tools.registry.ToolSpec`）。"""

    datasources: list[Any] = field(default_factory=list)
    """数据源对象：产出中性结构化数据，由宿主映射到模型。"""

    prompt_fragments: list[Any] = field(default_factory=list)
    """提示词片段贡献者。"""

    hooks: dict[str, HookHandler] = field(default_factory=dict)
    """hooks[事件名] = 处理函数。"""

    startup: Callable[[], Any] | None = None
    shutdown: Callable[[], Any] | None = None

    entry_path: Path | None = None
    """入口模块文件（目录插件，manifest 里声明 entry）；代码内注册的插件为 None。"""

    module_loaded: bool = False
    """入口模块是否已导入并收集贡献（避免重复加载）。"""

    state: PluginState = PluginState.DISCOVERED
    error: str = ""


class PluginRegistry:
    """三层插件注册表 + 能力索引。"""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginRegistration] = {}
        # 运行期的启用覆盖：缺省时以 manifest.enabled 为默认值。
        # 不用“disabled 集合”是因为那样无法表达“默认禁用、但用户显式启用”。
        self._enabled_overrides: dict[str, bool] = {}

    # ---------------- 注册 ----------------

    def register(self, registration: PluginRegistration) -> None:
        """注册一个插件；同 id 重复注册以最后一次为准（便于测试替换）。"""
        manifest = registration.manifest

        # 权限强制：只读插件不可能带写路径（声明自相矛盾时直接拒绝）
        perms = manifest.permissions
        if not perms.filesystem.write and perms.filesystem.write_paths:
            raise ValueError(
                f"插件 {manifest.id} 声明了写路径但 filesystem.write=false："
                "只读插件的写声明无效，请修正 manifest（AGENTS.md §9.3）"
            )

        if manifest.id in self._plugins:
            logger.warning("插件 %s 重复注册，覆盖前一份", manifest.id)
        self._plugins[manifest.id] = registration
        logger.info(
            "注册插件 %s（layer=%s，能力=%s）",
            manifest.id,
            manifest.layer.value,
            ", ".join(c.value for c in manifest.capabilities) or "无",
        )

    # ---------------- 查询 ----------------

    def get(self, plugin_id: str) -> PluginRegistration | None:
        return self._plugins.get(plugin_id)

    def all(self) -> list[PluginRegistration]:
        """全部插件（按 layer 与 loading_order 稳定排序，供管理 UI 展示）。"""
        layer_rank = {PluginLayer.CORE: 0, PluginLayer.BUILTIN: 1, PluginLayer.THIRD_PARTY: 2}
        return sorted(
            self._plugins.values(),
            key=lambda r: (layer_rank[r.manifest.layer], r.manifest.loading_order, r.manifest.id),
        )

    def enabled(self) -> list[PluginRegistration]:
        """启用中的插件。core 层永远启用（不可禁用）。"""
        return [reg for reg in self.all() if self._is_enabled(reg.manifest.id)]

    def _is_enabled(self, plugin_id: str) -> bool:
        """启用判定：core 恒真；否则“运行期覆盖”优先于 manifest 的默认值。"""
        reg = self._plugins.get(plugin_id)
        if reg is None:
            return False
        if reg.manifest.is_core:
            return True
        override = self._enabled_overrides.get(plugin_id)
        return reg.manifest.enabled if override is None else override

    def provider(self, capability: str, name: str = "", **kwargs: Any) -> Any:
        """按能力取实现。

        capability: 能力名（"llm" / "digital_human" / "warm_store" / "knowledge_store" …）
        name:       **实现名**，原样透传给插件工厂（空则用工厂的默认实现）

        取用规则：第一个**启用中**且提供该能力的插件胜出（按 layer/loading_order 排序）——
        插件数量有限，不做二级索引，保持逻辑简单可读。

        例：`registry.provider("llm", "dashscope", api_key="...")`
        """
        for reg in self.enabled():
            factory = reg.providers.get(capability)
            if factory is not None:
                return factory(name, **kwargs)
        raise KeyError(f"没有启用的插件提供能力：{capability}")

    # ---------------- 聚合视图 ----------------

    def tools(self) -> list[Any]:
        """全部启用插件的工具（`ToolSpec`），供构建 `ToolRegistry`。"""
        out: list[Any] = []
        for reg in self.enabled():
            out.extend(reg.tools)
        return out

    def datasources(self) -> list[Any]:
        """全部启用插件的数据源。"""
        out: list[Any] = []
        for reg in self.enabled():
            out.extend(reg.datasources)
        return out

    def hook_handlers(self, event: str) -> list[HookHandler]:
        """某事件的全部处理器（按插件 loading_order 排序）。"""
        out: list[HookHandler] = []
        for reg in self.enabled():
            handler = reg.hooks.get(event)
            if handler is not None:
                out.append(handler)
        return out

    def capabilities_summary(self) -> dict[str, list[str]]:
        """能力名 → 提供者插件 id 列表（管理 UI 用）。"""
        summary: dict[str, list[str]] = {}
        for reg in self.all():
            for capability in reg.providers:
                summary.setdefault(capability, []).append(reg.manifest.id)
        return summary

    # ---------------- 启停 ----------------

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """启用/禁用插件（写入运行期覆盖）；core 层拒绝禁用。"""
        reg = self._plugins.get(plugin_id)
        if reg is None:
            raise KeyError(f"插件不存在：{plugin_id}")
        if not enabled and reg.manifest.is_core:
            raise ValueError(f"插件 {plugin_id} 属于 core 层，不可禁用（AGENTS.md §9.2）")
        self._enabled_overrides[plugin_id] = enabled

    def __len__(self) -> int:
        return len(self._plugins)


# ---------------- 全局单例（与 app.mcp.manager 同范式）----------------

_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """取全局注册表（不存在则创建）。"""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def set_registry(registry: PluginRegistry | None) -> None:
    """替换全局注册表（测试用；传 None 表示下次 get 时重建）。"""
    global _registry
    _registry = registry
