"""插件生命周期管理：发现 → 排序 → setup → start → shutdown。

与 `app.mcp.manager.McpManager` 同范式（`AGENTS.md §9.3`）：
挂到 `main.py` 的 lifespan，**任一插件失败都不阻断宿主启动**（记 warning 并标记 FAILED）。

标签层（§9.7）：本模块**不实现插件热重载**——开发期用 `uvicorn --reload` 即可。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType

from app.plugins.capabilities import PluginState
from app.plugins.context import PluginContext
from app.plugins.manifest import MANIFEST_FILENAME, load_manifest, sort_manifests
from app.plugins.registry import PluginRegistration, PluginRegistry, get_registry

logger = logging.getLogger(__name__)


class PluginManager:
    """插件的发现、加载与生命周期。"""

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        *,
        data_dir: str | Path = "data",
        plugin_dirs: list[str | Path] | None = None,
    ) -> None:
        # 注意：不能用 `registry or get_registry()`——空注册表是 falsy，
        # 会被误判为“未提供”而取到全局单例（踩过一次）。
        self.registry = registry if registry is not None else get_registry()
        self.data_dir = Path(data_dir)
        # 第三方插件目录（不存在则忽略）；与 data/presets、data/studio 同构
        self.plugin_dirs = [Path(p) for p in (plugin_dirs or [self.data_dir / "plugins"])]
        self._contexts: dict[str, PluginContext] = {}
        self._settings: dict[str, dict] = {}

    # ---------------- 发现 ----------------

    def discover(self) -> list[PluginRegistration]:
        """扫描插件目录，把第三方插件注册进注册表（内置插件由 `builtin.py` 注册）。

        发现阶段**只读 manifest**，不执行插件代码——避免"加载即执行"的风险。
        依赖缺失（`entry` 指向的文件不存在）的插件标记 FAILED 并跳过。
        """
        found: list[PluginRegistration] = []
        manifests = []
        for directory in self.plugin_dirs:
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if not child.is_dir() or not (child / MANIFEST_FILENAME).is_file():
                    continue
                try:
                    manifests.append(load_manifest(child))
                except Exception as exc:  # manifest 损坏不应阻断宿主
                    logger.warning("插件 manifest 载入失败（%s）：%s", child, exc)

        for manifest in sort_manifests(manifests):
            if self.registry.get(manifest.id) is not None:
                logger.info("插件 %s 已注册（内置优先），跳过目录版本", manifest.id)
                continue
            registration = PluginRegistration(manifest=manifest, state=PluginState.DISCOVERED)
            if manifest.entry:
                entry_path = None
                for directory in self.plugin_dirs:
                    candidate = directory / manifest.id / manifest.entry
                    if candidate.is_file():
                        entry_path = candidate
                        break
                if entry_path is None:
                    registration.state = PluginState.FAILED
                    registration.error = f"入口文件不存在：{manifest.entry}"
                    logger.warning("插件 %s 载入失败：%s", manifest.id, registration.error)
                else:
                    registration.entry_path = entry_path
            self.registry.register(registration)
            found.append(registration)
        return found

    # ---------------- 入口模块动态加载 ----------------

    @staticmethod
    def _import_entry_module(plugin_id: str, path: Path) -> ModuleType:
        """按文件路径导入插件入口模块（不污染 sys.path，也不要求是包）。"""
        module_name = f"hypra_plugin_{plugin_id.replace('-', '_').replace('.', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:   # pragma: no cover - 防御
            raise ImportError(f"无法加载插件入口：{path}")
        module = importlib.util.module_from_spec(spec)
        # 先入 sys.modules：模块内部的 dataclass / 相对引用需要它在册
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _collect_contributions(self, reg: PluginRegistration, ctx: PluginContext) -> None:
        """导入目录插件入口并收集它声明的贡献（约定入口暴露 `build(ctx)`）。"""
        if reg.entry_path is None or reg.module_loaded:
            return
        module = self._import_entry_module(reg.manifest.id, reg.entry_path)
        builder = getattr(module, "build", None)
        if builder is None:
            raise ValueError(
                f"插件 {reg.manifest.id} 的入口缺少 build(ctx) 函数（约定见 AGENTS.md §9.3）"
            )
        contributions = builder(ctx) or {}
        # 宿主只认这些桶；未知键静默忽略，避免插件声明错别字导致启动失败
        reg.providers.update(contributions.get("providers") or {})
        reg.tools.extend(contributions.get("tools") or [])
        reg.datasources.extend(contributions.get("datasources") or [])
        reg.prompt_fragments.extend(contributions.get("prompt_fragments") or [])
        reg.hooks.update(contributions.get("hooks") or {})
        if contributions.get("startup") is not None:
            reg.startup = contributions["startup"]
        if contributions.get("shutdown") is not None:
            reg.shutdown = contributions["shutdown"]
        reg.module_loaded = True

    # ---------------- 配置 ----------------

    def _load_settings(self, plugin_id: str) -> dict:
        """从 `data/plugins/<id>/settings.json` 读插件配置（没有则空）。

        配置与插件代码分离：第三方插件目录被删/替换时，用户填过的配置仍在 data 下。
        """
        path = self.data_dir / "plugins" / plugin_id / "settings.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # 配置损坏不应阻断启动
            logger.warning("插件 %s 配置读取失败：%s", plugin_id, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def save_settings(self, plugin_id: str, values: dict) -> Path:
        """保存插件配置（供管理 UI 的配置表单调用）。"""
        path = self.data_dir / "plugins" / plugin_id / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._settings[plugin_id] = values
        return path

    # ---------------- 生命周期 ----------------

    def setup_all(self, settings: dict[str, dict] | None = None) -> list[str]:
        """对全部启用插件调用 `setup(ctx)`（执行插件注册的能力挂载）。

        返回成功 setup 的插件 id 列表。
        """
        # 显式传入的配置是**合并**而非替换：否则会冲掉 save_settings() 刚写入的内存值，
        # 导致“运行期改了配置却不生效”（需重启）。
        if settings:
            self._settings.update(settings)
        ready: list[str] = []
        for reg in self.registry.enabled():
            if reg.state is PluginState.FAILED:
                continue
            try:
                ctx = PluginContext(
                    manifest=reg.manifest,
                    # 显式传入优先；否则读 data/plugins/<id>/settings.json
                    settings=self._settings.get(reg.manifest.id)
                    or self._load_settings(reg.manifest.id),
                    data_dir=self.data_dir,
                )
                self._contexts[reg.manifest.id] = ctx
                # 目录插件：动态导入入口模块并收集贡献（代码内注册的插件跳过）
                self._collect_contributions(reg, ctx)
                if reg.startup is not None:
                    reg.startup()
                reg.state = PluginState.LOADED
                ready.append(reg.manifest.id)
            except Exception as exc:
                reg.state = PluginState.FAILED
                reg.error = str(exc)
                logger.warning("插件 %s setup 失败（不阻断宿主）：%s", reg.manifest.id, exc)
        return ready

    def start_all(self) -> list[str]:
        """把 LOADED 状态推进到 STARTED（真正的运行期资源在此获取）。"""
        started: list[str] = []
        for reg in self.registry.enabled():
            if reg.state is not PluginState.LOADED:
                continue
            reg.state = PluginState.STARTED
            started.append(reg.manifest.id)
        if started:
            logger.info("插件启动完成（%d 个）：%s", len(started), ", ".join(started))
        return started

    def shutdown_all(self) -> None:
        """逆序调用 `shutdown()`，释放插件资源。"""
        for reg in reversed(self.registry.all()):
            if reg.state is not PluginState.STARTED:
                continue
            try:
                if reg.shutdown is not None:
                    reg.shutdown()
            except Exception as exc:  # 关闭失败只记日志
                logger.warning("插件 %s 关闭异常：%s", reg.manifest.id, exc)
            finally:
                reg.state = PluginState.DISCOVERED

    # ---------------- 状态视图 ----------------

    def context_of(self, plugin_id: str) -> PluginContext | None:
        """取某插件的运行上下文（管理 UI / 测试用）。"""
        return self._contexts.get(plugin_id)

    def status(self) -> list[dict]:
        """插件状态快照（供 `/health` 与管理 UI）。"""
        registry = self.registry
        return [
            {
                "id": reg.manifest.id,
                "display_name": reg.manifest.display_name,
                "version": reg.manifest.version,
                "layer": reg.manifest.layer.value,
                "category": reg.manifest.category,
                # 已发现但未启用 → 展示为「已禁用」，避免 UI 把“没跑”说成“已就绪”
                "state": (
                    PluginState.DISABLED.value
                    if (reg.state is PluginState.DISCOVERED
                        and not registry._is_enabled(reg.manifest.id))  # noqa: SLF001
                    else reg.state.value
                ),
                "enabled": registry._is_enabled(reg.manifest.id),  # noqa: SLF001
                "capabilities": [c.value for c in reg.manifest.capabilities],
                "read_only": reg.manifest.read_only,
                "error": reg.error,
            }
            for reg in self.registry.all()
        ]

    def summary(self) -> dict:
        """计数摘要（`/health` 用）。"""
        regs = self.registry.all()
        return {
            "total": len(regs),
            "started": sum(1 for r in regs if r.state is PluginState.STARTED),
            "failed": sum(1 for r in regs if r.state is PluginState.FAILED),
            "by_layer": {
                layer: sum(1 for r in regs if r.manifest.layer.value == layer)
                for layer in ("core", "builtin", "third-party")
            },
        }


# ---------------- 全局单例（与 app.mcp.manager 同范式）----------------

_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager


def set_plugin_manager(manager: PluginManager | None) -> None:
    """替换全局管理器（测试用）。"""
    global _manager
    _manager = manager
