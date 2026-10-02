"""插件开发公开接口（Plugin SDK）——**第三方插件的唯一稳定入口**。

写插件的人只需要认这一个模块。它的存在有两个理由：

1. **稳定面**：插件不该 import 宿主内部模块（`app.api.*` / `app.graph.*` / 具体的存储实现…），
   否则宿主一次重构就把社区插件全部打断。这里把「插件确实需要的东西」集中导出，
   其余一律视为内部实现，随时可变。
2. **可发现性**：接口在哪、能贡献什么、权限怎么声明——看这一个文件就知道，
   不必去翻 `manager` / `registry` / `context` 三个模块再自己拼。

契约只有两条：

**① 入口函数**

    def build(ctx: PluginContext) -> dict:
        return {"tools": [...], "datasources": [...], ...}

`manifest.json` 的 `entry` 指向的模块里必须有 `build(ctx)`；宿主在 setup 阶段调用它，
把返回的 dict 按下面的桶收走。`build()` 会被**重复调用**（保存配置 / 启用插件 /
重扫目录都会重跑），因此它必须是幂等的：每次按当前 `ctx.settings` 重新算贡献即可，
不要在里面做一次性副作用（写文件、开线程要放在 `startup` 里）。

**② 贡献桶**（`build()` 返回值的键，未知键被忽略）

| 键 | 类型 | 作用 | 能力面 |
| --- | --- | --- | --- |
| `tools` | `list[ToolSpec]` | 注册 function calling 工具，模型可自主调用 | `tool` |
| `datasources` | `list[DataSource]`（有 `read() -> DataSourceSnapshot`） | 只读接入外部数据，产出中性结构，映射由宿主做 | `datasource` |
| `providers` | `dict[str, Callable]` | 按能力名提供实现工厂（`{"llm": create_llm_provider}`） | `provider` |
| `prompt_fragments` | `list` | 往注入点插提示词片段 | `prompt` |
| `hooks` | `dict[str, Callable]` | 订阅对话生命周期事件 | `hook` |
| `startup` | `Callable[[], None]` | 启动钩子（开线程 / 连服务放这里） | — |
| `shutdown` | `Callable[[], None]` | 关闭钩子（与 startup 配对） | — |

权限是**架构约束**：`manifest.json` 里 `filesystem.write = false` 的插件拿不到写句柄，
`ctx.write_text()` 会直接抛 `PermissionError`——不靠自觉。

完整说明、目录结构、调试方式与可复制的最小示例见 `docs/plugin-development.md`；
示例插件见 `docs/examples/plugin-hello/`。
"""

from __future__ import annotations

from typing import Any

# ---- 宿主门面：插件的运行时上下文（配置 / 日志 / 受权限约束的文件读写）----
from app.plugins.context import PluginContext, PermissionError_

# ---- 声明式元信息与校验（写 manifest.json 时可对照这些字段）----
from app.plugins.capabilities import (
    CapabilityType,
    FilesystemPermission,
    NetworkPermission,
    Permission,
    PluginLayer,
    PluginState,
)
from app.plugins.manifest import (
    MANIFEST_FILENAME,
    PluginManifest,
    dump_manifest,
    load_manifest,
)

# ---- 数据源中性契约：插件产出的就是这些结构，宿主负责映射到自己的模型 ----
from app.plugins.contracts import (
    DataSourceCharacter,
    DataSourceEntry,
    DataSourceMessage,
    DataSourceSession,
    DataSourceSnapshot,
)

# ---- function calling 工具契约 ----
from app.tools.registry import ToolContext, ToolSpec

#: `build(ctx)` 返回值里宿主认识的键（其余键静默忽略，防插件写错别字导致启动失败）。
CONTRIBUTION_KEYS = (
    "providers",
    "tools",
    "datasources",
    "prompt_fragments",
    "hooks",
    "startup",
    "shutdown",
)


def build_manifest(
    *,
    plugin_id: str,
    display_name: str,
    description: str = "",
    category: str = "tool",
    capabilities: list[str] | None = None,
    permissions: dict[str, Any] | None = None,
    settings_schema: dict[str, Any] | None = None,
    homepage: str = "",
    loading_order: int = 500,
) -> PluginManifest:
    """用代码构造一份 `PluginManifest`（写 manifest.json 前的自检 / 生成模板用）。

    `loading_order` 默认 500：排在全部内置插件之后——第三方插件不该抢在内置能力之前
    被取用（`PluginRegistry.provider()` 取的是第一个提供者）。
    """
    try:
        permission_model = (
            Permission.model_validate(permissions, strict=True) if permissions else Permission()
        )
        # 跨字段不变量：只读却声明写路径会在注册阶段抛错（见 Permission.check_consistent）。
        permission_model.check_consistent()
    except Exception as exc:  # noqa: BLE001 - 声明不合法属可修的错误
        raise ValueError(f"权限声明不合法：{exc}") from exc

    wanted = [item for item in (capabilities or []) if item in {c.value for c in CapabilityType}]
    return PluginManifest(
        id=plugin_id,
        display_name=display_name or plugin_id,
        layer=PluginLayer.THIRD_PARTY,
        category=(category or "tool").strip() or "tool",
        description=description.strip(),
        homepage=homepage.strip(),
        loading_order=loading_order,
        capabilities=[CapabilityType(item) for item in wanted],
        permissions=permission_model,
        settings_schema=settings_schema or {},
        entry="plugin.py",
    )


__all__ = [
    "CONTRIBUTION_KEYS",
    "MANIFEST_FILENAME",
    "CapabilityType",
    "DataSourceCharacter",
    "DataSourceEntry",
    "DataSourceMessage",
    "DataSourceSession",
    "DataSourceSnapshot",
    "FilesystemPermission",
    "NetworkPermission",
    "Permission",
    "PermissionError_",
    "PluginContext",
    "PluginLayer",
    "PluginManifest",
    "PluginState",
    "ToolContext",
    "ToolSpec",
    "build_manifest",
    "dump_manifest",
    "load_manifest",
]
