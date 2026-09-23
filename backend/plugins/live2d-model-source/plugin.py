"""数字人模型来源插件（只读，`AGENTS.md §9.10` 第 15 项）。

把「模型从哪来」做成可插拔的数据源：

    本机模型库 backend/data/avatar_models/  → 已安装的模型（可直接选用）
    外部清单文件（用户配置路径）            → 可获取的模型（作者 / 授权 / 获取地址）

**要解决的问题**：模型「有哪些可以用」原本硬编码在前端的常量表
（`frontend/lib/live2d/model-assets.ts`）里，加一个模型要改前端代码。

**两条边界，都是刻意的**：

1. **零写操作**：manifest 声明 `filesystem.write = false`，宿主因此不授予写句柄；
   即使本文件尝试写入，`ctx.write_text()` 也会抛 `PermissionError`。这不是约定，是架构约束。
2. **不代下载**：模型**不可自由分发**（授权与体积原因，见 `docs/license-compliance.md`），
   代下载会把「用户自己的选择」变成「本项目在分发第三方模型」，同时把只读的展示功能
   变成网络写操作面。所以本插件只回答「有哪些、谁的、什么条款、去哪拿」，
   实际获取由用户自行完成后走既有上传流程（`POST /media/avatar/models`）。

清单**只接受本地路径与 `file://`**：拉远程 JSON 是网络访问，而插件权限模型目前
只对文件系统有强制校验（`PluginContext` 的受控读 API），网络没有对应闸门——
声明 `network.hosts: []` 却真去发请求，等于把声明写成空话。需要社区清单时，
把 JSON 下到本地再配路径即可。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.digital_human.model_sources.registry import ModelSourceRegistry, build_default_sources

if TYPE_CHECKING:  # 仅供类型检查：运行时不导入宿主实现，避免插件与宿主强耦合
    from app.plugins.context import PluginContext

PLUGIN_ID = "live2d-model-source"

#: 模型库目录相对 `ctx.data_dir` 的默认位置（与宿主 `AVATAR_MODELS_DIR` 默认一致）
DEFAULT_MODELS_SUBDIR = "avatar_models"


def build(ctx: PluginContext) -> dict[str, Any]:
    """插件入口（宿主在 setup 阶段调用）：返回本插件对宿主的贡献。"""
    manifest_path = str(ctx.settings.get("manifest_path") or "").strip()

    configured_dir = str(ctx.settings.get("models_dir") or "").strip()
    models_dir = configured_dir or (ctx.data_dir / DEFAULT_MODELS_SUBDIR)

    registry = ModelSourceRegistry(
        build_default_sources(
            models_dir=models_dir, manifest_path=manifest_path, reader=ctx
        )
    )

    # 读一遍并记日志：setup 阶段就把「清单配了没、读到几条」暴露出来，
    # 否则用户只能通过界面猜为什么列表是空的
    for status in registry.status():
        if status["available"]:
            ctx.logger.info("模型来源 %s：%s（%d 条）", status["id"], status["description"], status["count"])
        else:
            ctx.logger.info("模型来源 %s 未配置：%s", status["id"], status["description"])

    return {"datasources": [registry]}
