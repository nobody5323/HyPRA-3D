"""插件管理 API（设计见 `AGENTS.md §9`）。

    GET    /plugins/{id}                插件列表 / 状态 / 能力索引（能力中心数据源）
    POST   /plugins/{id}/enabled        启用 / 禁用（core 层拒绝禁用）
    GET    /plugins/{id}/settings       读插件配置（含 settings_schema，前端据此渲染表单）
    PUT    /plugins/{id}/settings       写插件配置

    POST   /plugins/tavern-bridge/import    酒馆会话 → 长期记忆（P4：跨会话记忆构建）

**配置是声明式的**：插件不写前端表单，只给 JSON Schema，前端按 schema 渲染——
这是 `§9.5` 的落地（对标 Alife 用 `editorUI` 类型贡献 UI 的做法，但避免了运行时加载前端代码）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.memory.tavern_import import TavernMemoryImporter
from app.plugins.manager import get_plugin_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])

TAVERN_PLUGIN_ID = "tavern-bridge"


# =============================================================
# 请求模型
# =============================================================


class PluginToggleRequest(BaseModel):
    enabled: bool = Field(description="是否启用")


class PluginSettingsPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict, description="配置键值（按 settings_schema）")


# =============================================================
# 列表与启停
# =============================================================


@router.get("")
def list_plugins() -> dict:
    """插件列表（含状态、分层、能力索引）——`/health` 的完整版。"""
    manager = get_plugin_manager()
    return {
        "plugins": manager.status(),
        "summary": manager.summary(),
        "capabilities": manager.registry.capabilities_summary(),
    }


@router.post("/{plugin_id}/enabled")
def set_plugin_enabled(plugin_id: str, payload: PluginToggleRequest) -> dict:
    """启用 / 禁用插件。core 层不可禁用（返回 400）。

    状态**落盘**（`data/plugins/<id>/state.json`）：重启后保持，不会回弹到 manifest 默认值。
    启停同时立即生效——启用即 setup、禁用即停跑，界面与实际一致。
    """
    manager = get_plugin_manager()
    try:
        manager.set_enabled(plugin_id, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"id": plugin_id, "enabled": payload.enabled, "plugins": manager.status()}


# =============================================================
# 配置
# =============================================================


@router.get("/{plugin_id}/settings")
def get_plugin_settings(plugin_id: str) -> dict:
    """读插件配置 + 其 settings_schema（前端据此渲染表单）。"""
    manager = get_plugin_manager()
    registration = manager.registry.get(plugin_id)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}")
    return {
        "id": plugin_id,
        "values": manager._load_settings(plugin_id),  # noqa: SLF001 - 同一模块族的读接口
        "schema": registration.manifest.settings_schema,
        "permissions": registration.manifest.permissions.model_dump(mode="json"),
    }


@router.put("/{plugin_id}/settings")
def put_plugin_settings(plugin_id: str, payload: PluginSettingsPayload) -> dict:
    """保存插件配置；保存后立刻重新 setup，让新配置生效。"""
    manager = get_plugin_manager()
    registration = manager.registry.get(plugin_id)
    if registration is None:
        raise HTTPException(status_code=404, detail=f"插件不存在：{plugin_id}")

    manager.save_settings(plugin_id, payload.values)
    # 目录插件的 build(ctx) 可能要重跑（如数据目录变更后重新读取）；
    # 用 reload_all 而非 setup_all：只 setup 会把插件退回 LOADED，
    # 界面上的「运行中」会变成「已加载」——刚存完配置就像能力掉了。
    manager.reload_all()
    return {"id": plugin_id, "values": payload.values, "plugins": manager.status()}


# =============================================================
# 酒馆会话 → 长期记忆（P4）
# =============================================================


def _find_tavern_datasource() -> Any:
    """取已接入的酒馆数据源（插件未启用/未配置时抛出可读错误）。"""
    manager = get_plugin_manager()
    registration = manager.registry.get(TAVERN_PLUGIN_ID)
    if registration is None:
        raise HTTPException(status_code=404, detail="酒馆接入插件未安装")
    if not registration.datasources:
        raise HTTPException(
            status_code=409,
            detail="酒馆接入插件尚未生效：请先在能力中心启用它并填写酒馆数据目录",
        )
    return registration.datasources[0]


@router.get("/tavern-bridge/status")
def tavern_bridge_status(
    companion_id: str = Query("", description="查询该陪伴对象已导入的会话记录"),
) -> dict:
    """酒馆接入状态：能读到什么 + 已导入多少（供 UI 显示进度）。"""
    source = _find_tavern_datasource()
    snapshot = source.read()

    imported: list[str] = []
    if companion_id:
        from app.config import get_settings

        importer = TavernMemoryImporter(
            memory_store=None,  # 仅查询记录，不写记忆
            state_path=get_settings().tavern_import_state_file,
        )
        imported = importer.imported_sessions(companion_id)

    return {
        "root": str(source.root) if source.root else "",
        "available": snapshot.counts,
        "warnings": snapshot.warnings[:20],
        "imported_sessions": imported,
        "characters": [character.name for character in snapshot.characters],
    }


@router.post("/tavern-bridge/import")
def import_tavern_memory(
    companion_id: str = Query(..., description="导入到哪个陪伴对象的记忆命名空间"),
    force: bool = Query(False, description="忽略已导入记录，重新导入"),
) -> dict:
    """把酒馆会话导入为 HyPRA 的长期记忆（`AGENTS.md §8.2` 的跨会话记忆）。

    走同一条记忆写入链路（`MemoryStore.remember_turn`）：
    情景记忆进温层向量库、语义事实进冷层表——与日常对话产生的记忆同构，
    因此召回时无需区分来源。
    """
    from app.api.chat import get_memory_store
    from app.config import get_settings

    source = _find_tavern_datasource()
    memory_store = get_memory_store()
    if memory_store is None:
        raise HTTPException(status_code=503, detail="记忆服务未就绪")

    snapshot = source.read()
    importer = TavernMemoryImporter(
        memory_store, state_path=get_settings().tavern_import_state_file
    )
    result = importer.import_sessions(
        snapshot.sessions, companion_id=companion_id, force=force
    )
    return {"companion_id": companion_id, **result.as_dict()}
