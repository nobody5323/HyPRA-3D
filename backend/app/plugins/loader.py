"""插件导入：把「写在别处的插件目录」接进宿主。

与已删除的 AI 生成插件写入器（`writer.py`）的区别是**来源**：这里的内容是用户自己
写的（在编辑器 / 自己的仓库里 / 别人给的压缩包解开后），宿主的职责只是**校验 + 搬运**，
不生成任何代码，也不替用户决定内容。

两条路径（`AGENTS.md §9.3` / `docs/plugin-development.md`）：

- **零拷贝**：把插件所在目录填进 `PLUGIN_EXTRA_DIRS`，`discover()` 直接扫它——
  开发期改完代码点「重新扫描」即可，宿主不碰你的文件；
- **导入**：本模块，把一份插件目录**复制**进 `PLUGINS_DIR`（`data/plugins/<id>/`），
  适合「拿到一个插件包，想装进程序里」的场景。

导入的硬约束（都是刻意的）：

1. **只收 third-party 层**：`core` / `builtin` 只能随项目分发——否则「核心不可禁用」
   这条约束可以被一次导入绕过（与 `docs/plugin-market.md` §11 同口径）；
2. **先校验后落盘**：id 合法、manifest 可解析、权限声明自洽、入口文件存在且能
   `ast.parse`。任何一条不过就**什么都不写**——留下半个插件目录，宿主下次启动会把它
   标记成 FAILED 并常驻在能力中心里，用户只能手工去删；
3. **先写暂存目录再整体替换**：写一半的中断不会留下半成品；
4. **不做沙箱承诺**：插件代码在宿主进程内执行（`AGENTS.md §8.5 / §9.7`），
   导入 = 信任这份代码。界面必须把这句话说清楚，不许暗示有隔离。
"""

from __future__ import annotations

import ast
import logging
import os
import shutil
from pathlib import Path

from app.plugins.capabilities import PluginLayer
from app.plugins.manifest import MANIFEST_FILENAME, PluginManifest, load_manifest

logger = logging.getLogger(__name__)

#: 导入时跳过的目录 / 文件（构建缓存与版本控制元数据，拷过去只会污染插件目录）。
_SKIP_NAMES = frozenset({"__pycache__", ".git", ".venv", "node_modules", ".pytest_cache"})


class PluginImportError(ValueError):
    """插件导入被拒（源目录不合法 / id 冲突 / 层不合法 / 入口有问题）。"""


def plugin_dir(plugins_dir: str | Path, plugin_id: str) -> Path:
    """插件目录（`<plugins_dir>/<id>/`）。目录拼法只有这一处，免得界面显示的路径与实际落盘的不一致。"""
    return Path(plugins_dir) / plugin_id


def import_plugin_dir(
    source: str | Path,
    plugins_dir: str | Path,
    *,
    known_ids: set[str] | None = None,
    replace: bool = False,
) -> PluginManifest:
    """把 `source` 指向的插件目录导入到 `plugins_dir/<id>/`，返回装载后的 manifest。

    参数:
        source: 插件源目录（其下必须有 `manifest.json`）。
        known_ids: 已注册且**仍然占着位置**的插件 id；命中即拒绝。
        replace: 目标目录已存在时是否整体替换（默认拒绝，避免误覆盖用户已有的插件）。

    抛出:
        PluginImportError: 源目录不合法 / id 冲突 / 层不合法 / 入口缺失或语法错误 / 写入失败。
    """
    src = Path(source).expanduser()
    if not src.is_dir():
        raise PluginImportError(f"插件目录不存在：{src}")
    if not (src / MANIFEST_FILENAME).is_file():
        raise PluginImportError(f"目录里没有 {MANIFEST_FILENAME}：{src}")

    try:
        manifest = load_manifest(src)
    except Exception as exc:  # noqa: BLE001 - manifest 不合法属用户可修的错误
        raise PluginImportError(f"{MANIFEST_FILENAME} 不合法：{exc}") from exc

    if manifest.layer is not PluginLayer.THIRD_PARTY:
        raise PluginImportError(
            f"只能导入 third-party 层插件，而 {manifest.id} 声明为 {manifest.layer.value}"
            "（core / builtin 只能随项目分发，见 AGENTS.md §9.2）"
        )

    try:
        manifest.permissions.check_consistent()
    except ValueError as exc:
        raise PluginImportError(f"权限声明不合法：{exc}") from exc

    entry_path = (src / manifest.entry) if manifest.entry else None
    if manifest.entry and not entry_path.is_file():
        raise PluginImportError(f"入口文件不存在：{manifest.entry}")
    if entry_path is not None:
        _check_python_syntax(entry_path)

    target_dir = plugin_dir(plugins_dir, manifest.id)
    # 源目录就在目标位置（用户直接指向 data/plugins/<id>）时无需搬运，直接放行。
    # 这一条必须排在「目标已存在」之前——它本来就存在，那是它自己。
    if src.resolve() == target_dir.resolve():
        logger.info("插件 %s 已在目标目录，跳过搬运", manifest.id)
        return manifest
    if _is_within(target_dir, src) or _is_within(src, target_dir):
        raise PluginImportError(f"源目录与目标目录不能互相包含：{src} → {target_dir}")

    # 「目标目录已存在」与「id 被占用」是两件事，报错文案也要分开：
    # - 目录在 → 是同一个插件的重复导入，用户该做的是勾「替换」或换 id；
    # - 目录不在却被占用 → 那是内置插件（代码内注册）或幽灵登记，只能换 id。
    # 顺序很重要：先判目录，否则「替换」这条出路会被「占用」拦死（永远走不到）。
    target_existed = target_dir.exists()
    if target_existed and not replace:
        raise PluginImportError(f"插件目录已存在：{target_dir}（换个 id，或勾选替换）")
    if known_ids and manifest.id in known_ids and not target_existed:
        raise PluginImportError(f"插件 id「{manifest.id}」已被内置或已导入的插件占用，请换个 id")

    # 先写临时目录再整体替换：写一半的中断不会留下「有 manifest 没入口」的半成品。
    # 整段包 try：mkdir 失败（无权限 / 保留名 / 超长路径）也是可展示的错误，不该以裸 500 冒出去。
    staging = target_dir.with_name(f".{manifest.id}.importing")
    backup: Path | None = None
    try:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        _copy_tree(src, staging)

        if target_existed:  # replace=True 时先挪走旧目录，替换失败还能还原
            backup = target_dir.with_name(f".{manifest.id}.backup")
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            os.replace(target_dir, backup)

        os.replace(staging, target_dir)

        restored = load_manifest(target_dir)
        # 回读之后再查一遍注册表的不变量：写盘前的校验是同一份（Permission.check_consistent），
        # 但磁盘上的 manifest 才是真相，多一道防线不花钱。
        restored.permissions.check_consistent()
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if backup is not None and backup.exists():
            # 替换失败 → 把用户原来的插件放回去，不留一个「两边都没有」的坑
            shutil.rmtree(target_dir, ignore_errors=True)
            os.replace(backup, target_dir)
        elif not target_existed:
            # 目标本来是空的，撤掉刚替换过去的那份
            shutil.rmtree(target_dir, ignore_errors=True)
        # target_existed 且 backup 仍为 None → 旧目录还没被动过，保持原样
        if isinstance(exc, PluginImportError):
            raise
        raise PluginImportError(f"插件写入失败：{exc}") from exc
    finally:
        if backup is not None and backup.exists():
            shutil.rmtree(backup, ignore_errors=True)

    logger.info("插件已导入：%s", target_dir)
    return restored


def _check_python_syntax(path: Path) -> None:
    """入口文件语法预检（`ast.parse`）。

    只做**语法**层面的检查：宿主在用之前无法证明一段代码安全（启用即 import 并执行，
    见 `AGENTS.md §8.5`），所以真正的防线是「用户自己写 / 自己审阅」，不是静态扫描。
    这一道只是为了在**落盘前**给出可读的错误，而不是让宿主启动时才报 FAILED。
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PluginImportError(f"入口文件读取失败：{path.name}（{exc}）") from exc
    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise PluginImportError(
            f"入口文件有语法错误：{path.name} 第 {exc.lineno} 行 {exc.msg}"
        ) from exc


def _copy_tree(source: Path, target: Path) -> None:
    """把插件目录拷进暂存目录，跳过构建缓存与版本控制元数据。"""
    for item in sorted(source.iterdir()):
        if item.name in _SKIP_NAMES:
            continue
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(
                item, destination, ignore=shutil.ignore_patterns(*_SKIP_NAMES)
            )
        else:
            shutil.copy2(item, destination)


def _is_within(child: Path, parent: Path) -> bool:
    """`child` 是否在 `parent` 之内（含相等）。"""
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True
