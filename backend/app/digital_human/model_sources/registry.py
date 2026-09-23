"""模型来源聚合：把多个来源的清单合成一张去重后的表。

去重规则是这里唯一有判断的地方（见 `descriptors()`），其余都是转发。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.digital_human.model_sources.base import ModelDescriptor, ModelSource
from app.digital_human.model_sources.local_library import LocalLibrarySource
from app.digital_human.model_sources.remote_manifest import RemoteManifestSource

logger = logging.getLogger(__name__)


def build_default_sources(
    *,
    models_dir: str | Path = "",
    manifest_path: str = "",
    reader: Any = None,
) -> list[ModelSource]:
    """内置来源组合（本机库 + 清单）。

    插件被禁用/未安装时 API 退化为用它——与分词、解析插件同理：
    注册表是**能力账本**，不是运行时唯一通路。

    `reader` 是宿主的受控读接口（`PluginContext`）：插件贡献的实例带上它，
    读文件要过权限白名单；宿主自己构造的实例不传（宿主不受插件权限约束）。
    """
    return [
        LocalLibrarySource(models_dir, reader=reader),
        RemoteManifestSource(manifest_path, reader=reader),
    ]


class ModelSourceRegistry:
    """把若干来源聚合成一张清单。"""

    def __init__(self, sources: Sequence[ModelSource] = ()) -> None:
        self._sources = list(sources)

    def all(self) -> list[ModelSource]:
        return list(self._sources)

    def descriptors(self) -> list[ModelDescriptor]:
        """全部来源的模型，按 id 去重。

        **冲突时已安装的胜出**（本机真实存在的那份更权威），
        但清单侧的授权 / 作者 / 获取地址会**合并进来**——那些信息本机库里没有，
        而它们恰恰是用户判断「这个模型能不能用」的依据（§6 合规红线）。
        """
        merged: dict[str, ModelDescriptor] = {}
        for source in self._sources:
            if not source.available():
                continue
            for descriptor in source.list_models():
                existing = merged.get(descriptor.id)
                merged[descriptor.id] = (
                    descriptor if existing is None else _merge(existing, descriptor)
                )
        # 已安装的排前面（用户最可能选它们），其余按 id 稳定排序
        return sorted(merged.values(), key=lambda item: (not item.installed, item.id))

    def status(self) -> list[dict]:
        """来源级状态（供 UI 说明「清单配了没、读到几条」）。"""
        out: list[dict] = []
        for source in self._sources:
            available = source.available()
            out.append(
                {
                    "id": source.name,
                    "available": available,
                    "description": source.describe(),
                    "count": len(source.list_models()) if available else 0,
                }
            )
        return out


def _merge(installed: ModelDescriptor, other: ModelDescriptor) -> ModelDescriptor:
    """合并同一 id 的两条描述：已装的为主，另一条补全元信息。

    返回**新对象**而不是原地改：描述符可能被来源缓存复用，就地改会污染来源。
    """
    primary, secondary = (
        (installed, other) if installed.installed else (other, installed)
    )
    return ModelDescriptor(
        id=primary.id,
        display_name=primary.display_name or secondary.display_name,
        kind=primary.kind or secondary.kind,
        source=primary.source,
        installed=installed.installed or other.installed,
        entry=primary.entry or secondary.entry,
        description=primary.description or secondary.description,
        preview_url=primary.preview_url or secondary.preview_url,
        author=primary.author or secondary.author,
        license=primary.license or secondary.license,
        homepage=primary.homepage or secondary.homepage,
        extra={**secondary.extra, **primary.extra},
    )
