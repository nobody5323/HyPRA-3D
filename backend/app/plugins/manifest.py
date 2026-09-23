"""插件 manifest：声明式描述 + 校验。

对标 Alife 的 `ModuleAttribute`（见 `AGENTS.md §9.3` 的字段映射表）：

    Alife 字段           →  HyPRA 字段
    name/description/url →  display_name/description/homepage
    launchOrder          →  loading_order
    defaultCategory      →  category
    editorUI (Type)      →  settings_schema (JSON Schema)   ← 宿主渲染表单，插件不写前端
    （DI 构造注入）        →  PluginContext
    Awake/Start/Destroy  →  setup/start/shutdown

第三方插件从 `manifest.json` 载入；内置插件直接用 Python 构造（见 `builtin.py`），
两者最终都归一化为同一个 `PluginManifest`。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.plugins.capabilities import CapabilityType, Permission, PluginLayer

# 插件 id 规范：小写字母、数字、连字符/下划线；必须以字母或数字开头
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

MANIFEST_FILENAME = "manifest.json"


class PluginManifest(BaseModel):
    """插件声明。"""

    id: str = Field(description="插件唯一标识（小写字母/数字/._-，≤64 字符）")
    display_name: str = Field(description="展示名（管理 UI）")
    version: str = Field(default="0.0.0", description="语义化版本")
    layer: PluginLayer = Field(
        default=PluginLayer.BUILTIN,
        description="必需性分层：core 不可禁用；builtin 可禁用/替换；third-party 用户安装",
    )
    category: str = Field(
        default="",
        description="能力分类（管理 UI 分组）：provider / tool / datasource / memory / media …",
    )
    description: str = Field(default="", description="一句话说明")
    homepage: str = Field(default="", description="来源地址（第三方插件必填，供合规追溯）")

    loading_order: int = Field(
        default=100,
        description="加载顺序（小者先；对标 Alife launchOrder 与 ST loading_order）",
    )
    capabilities: list[CapabilityType] = Field(
        default_factory=list, description="本插件贡献的能力面"
    )
    permissions: Permission = Field(
        default_factory=Permission, description="权限声明（宿主强制校验）"
    )
    settings_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="配置的 JSON Schema；宿主据此渲染设置表单（插件不写前端代码）",
    )

    entry: str | None = Field(
        default=None,
        description="第三方插件入口文件（相对插件目录）；内置插件为 None（代码内注册）",
    )
    enabled: bool = Field(default=True, description="默认是否启用")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not _ID_PATTERN.match(value):
            raise ValueError(f"非法的插件 id：{value!r}（应为小写字母/数字/._-，≤64 字符）")
        return value

    @property
    def is_core(self) -> bool:
        """core 层插件不可禁用（`AGENTS.md §9.2`）。"""
        return self.layer is PluginLayer.CORE

    @property
    def read_only(self) -> bool:
        """是否为只读插件（不写文件系统）。"""
        return self.permissions.is_read_only


def load_manifest(path: str | Path) -> PluginManifest:
    """从插件目录的 `manifest.json` 载入声明。"""
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"未找到插件 manifest：{manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return PluginManifest.model_validate(data)


def dump_manifest(manifest: PluginManifest, path: str | Path) -> None:
    """把声明写回 `manifest.json`（供用户插件模板 / 导出用）。"""
    target = Path(path)
    if target.is_dir():
        target = target / MANIFEST_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sort_manifests(manifests: list[PluginManifest]) -> list[PluginManifest]:
    """按 loading_order 排序（同序时按 id 稳定排序）。

    与 ST 的 `sortManifestsByOrder` 同语义（`loading_order` 小者先加载）。
    """
    return sorted(manifests, key=lambda m: (m.loading_order, m.id))
