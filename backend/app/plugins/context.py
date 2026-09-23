"""PluginContext：宿主交给插件的门面。

对标 Alife 的依赖注入容器（`AGENTS.md §9.3`）：插件**不直接 import 业务模块**，
而是从 context 取用宿主提供的能力。这样插件与宿主解耦，替换实现不影响插件。

权限在这里落地：`read_text()` / `write_text()` 会校验插件 manifest 的权限声明，
未授权的访问直接抛 `PermissionError`——只读插件拿不到写能力是**代码保证**。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.plugins.capabilities import PluginLayer
from app.plugins.manifest import PluginManifest


class PermissionError_(PermissionError):
    """插件越权访问（继承内置 PermissionError，便于调用方统一捕获）。"""


@dataclass
class PluginContext:
    """插件运行上下文。

    由宿主在 `setup(ctx)` 时构造并传入；插件把需要的东西存下来即可：

        def setup(ctx):
            ctx.settings["data_dir"]      # 用户配置
            ctx.log.info("已就绪")
            ctx.read_text(path)           # 受权限约束的文件读取
    """

    manifest: PluginManifest
    settings: dict[str, Any] = field(default_factory=dict)
    """该插件的配置值（由宿主按 settings_schema 从 data 载入）。"""

    data_dir: Path = field(default_factory=lambda: Path("data"))
    """宿主数据目录（插件可在此保存自己的状态）。"""

    logger: logging.Logger = field(init=False)
    """插件专用 logger（自动带插件名前缀，便于排查）。"""

    def __post_init__(self) -> None:
        self.logger = logging.getLogger(f"plugin.{self.manifest.id}")

    # ---------------- 权限受控的文件访问 ----------------

    @property
    def is_read_only(self) -> bool:
        return self.manifest.read_only

    def _resolve_allowed(self, path: str | Path, *, write: bool) -> Path:
        """把路径解析为绝对路径，并校验是否在权限白名单内。"""
        target = Path(path).expanduser().resolve()
        perms = self.manifest.permissions

        if write:
            if not perms.filesystem.write:
                raise PermissionError_(
                    f"插件 {self.manifest.id} 声明为只读（filesystem.write=false），"
                    f"不允许写入：{target}"
                )
            allowed = self._expand(perms.filesystem.write_paths)
        else:
            allowed = self._expand(perms.filesystem.read)

        if not allowed:
            raise PermissionError_(
                f"插件 {self.manifest.id} 未声明{'写' if write else '读'}权限：{target}"
            )
        for root in allowed:
            if target == root or root in target.parents:
                return target
        raise PermissionError_(
            f"插件 {self.manifest.id} 的{'写' if write else '读'}路径越权：{target}\n"
            f"允许范围：{[str(p) for p in allowed]}"
        )

    def _expand(self, paths: list[str]) -> list[Path]:
        """把白名单里的 ${setting} 占位符替换为配置值。"""
        out: list[Path] = []
        for raw in paths:
            text = raw
            for key, value in self.settings.items():
                text = text.replace(f"${{{key}}}", str(value))
            if "${" in text:   # 仍有未解析的占位符 → 说明用户没配该项，跳过
                self.logger.warning("权限白名单项未解析（缺配置）：%s", raw)
                continue
            out.append(Path(text).expanduser().resolve())
        return out

    # ---------------- 受控的文件读写 ----------------

    def read_text(self, path: str | Path, encoding: str = "utf-8") -> str:
        """读文本（需 `filesystem_read` 白名单覆盖该路径）。"""
        target = self._resolve_allowed(path, write=False)
        return target.read_text(encoding=encoding)

    def read_bytes(self, path: str | Path) -> bytes:
        """读二进制（需 `filesystem_read` 白名单覆盖该路径）。"""
        target = self._resolve_allowed(path, write=False)
        return target.read_bytes()

    def read_json(self, path: str | Path, encoding: str = "utf-8") -> Any:
        """读 JSON（需读权限）。"""
        import json

        return json.loads(self.read_text(path, encoding=encoding))

    def list_dir(self, path: str | Path) -> list[Path]:
        """列目录（需读权限覆盖该目录）。"""
        target = self._resolve_allowed(path, write=False)
        if not target.is_dir():
            return []
        return sorted(target.iterdir())

    def write_text(self, path: str | Path, text: str, encoding: str = "utf-8") -> None:
        """写文本（需 `filesystem_write=True` 且白名单覆盖该路径）。"""
        target = self._resolve_allowed(path, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding=encoding)

    def own_data_dir(self) -> Path:
        """插件自有的状态目录（`data/plugins/<id>/`），无需额外权限声明。"""
        target = self.data_dir / "plugins" / self.manifest.id
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ---------------- 能力查询 ----------------

    def provider(self, capability: str, name: str = "") -> Any:
        """取宿主中某能力的实现（插件之间不直接依赖，只经此取用）。"""
        from app.plugins.registry import get_registry

        return get_registry().provider(capability, name)

    @property
    def layer(self) -> PluginLayer:
        return self.manifest.layer
