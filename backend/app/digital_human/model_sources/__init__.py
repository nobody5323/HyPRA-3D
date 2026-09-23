"""数字人模型来源（`AGENTS.md §9.10` 第 15 项）。

| 模块 | 职责 |
|---|---|
| `base.py` | `ModelSource` 接口与 `ModelDescriptor` 中性对象 |
| `local_library.py` | 本机模型库（已上传的那批） |
| `remote_manifest.py` | 外部清单文件（只读，不代下载） |
| `registry.py` | 聚合与去重 |

**要解决的问题**：模型「有哪些可以用」原本硬编码在前端常量表里，
加一个模型要改前端代码。现在来源可插拔，前端只消费统一清单。

**边界（合规）**：本模块只回答「有哪些、谁的、什么条款、去哪拿」，
不下载、不代分发——模型不可自由分发，见 `docs/license-compliance.md`。
"""

from app.digital_human.model_sources.base import ModelDescriptor, ModelSource
from app.digital_human.model_sources.local_library import LocalLibrarySource
from app.digital_human.model_sources.registry import ModelSourceRegistry, build_default_sources
from app.digital_human.model_sources.remote_manifest import (
    MANIFEST_VERSION,
    RemoteManifestSource,
)

__all__ = [
    "MANIFEST_VERSION",
    "LocalLibrarySource",
    "ModelDescriptor",
    "ModelSource",
    "ModelSourceRegistry",
    "RemoteManifestSource",
    "build_default_sources",
]
