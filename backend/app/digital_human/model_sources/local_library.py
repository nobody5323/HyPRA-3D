"""本机模型库来源：已上传到 `data/avatar_models/` 的模型。

它把既有的 `AvatarModelStore` **包装**成来源接口——不重写存储、不改上传流程，
只是让「已安装的模型」与其他来源出现在同一张清单里。

**两条读取路径，都有存在的理由**：

- `reader`（宿主的受控读接口，即 `PluginContext`）：插件贡献的实例走这条，
  读文件要过 `permissions.filesystem.read` 白名单——§9.3 说权限是架构约束，
  那插件就不能绕过它自己去 `Path.read_text`，否则约束只是纸面承诺；
- 无 `reader`：宿主自己构造的实例（插件被禁用时的退化路径）走这条。
  宿主不受插件权限约束，这是有意的。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.digital_human.model_sources.base import ModelDescriptor, ModelSource

logger = logging.getLogger(__name__)


class LocalLibrarySource(ModelSource):
    """本机模型库（上传即入库的那批）。"""

    name = "local-library"

    def __init__(self, root: str | Path = "", *, reader: Any = None) -> None:
        self.root = Path(root) if str(root).strip() else None
        self._reader = reader

    def available(self) -> bool:
        return self.root is not None

    def list_models(self) -> list[ModelDescriptor]:
        if self.root is None:
            return []
        models = self._read_via_reader() if self._reader is not None else self._read_directly()
        return [_describe(model) for model in models]

    def describe(self) -> str:
        return f"本机模型库（{self.root}）" if self.root else "未配置本机模型库"

    # ---------------- 两条读取路径 ----------------

    def _read_directly(self):
        # 延迟导入：model_store 依赖 json / zipfile，而本源在未配置时不该被加载
        from app.digital_human.model_store import AvatarModelStore

        try:
            return AvatarModelStore(self.root).list_models()
        except Exception as exc:  # 目录损坏不该让整张清单空掉
            logger.warning("本机模型库读取失败（%s）：%s", self.root, exc)
            return []

    def _read_via_reader(self):
        """经宿主的受控读 API 读元数据（越权文件既看不到也读不到）。"""
        # 元数据文件名复用 model_store 的常量：两处各担一份会在改名时静默失配
        from app.digital_human.model_store import MODEL_META_FILE, AvatarModel

        assert self.root is not None  # noqa: S101 - available() 已保证
        try:
            entries = self._reader.list_dir(self.root)
        except PermissionError as exc:
            logger.warning("模型库目录不在插件读权限内（%s）：%s", self.root, exc)
            return []
        except Exception as exc:
            logger.warning("模型库目录读取失败（%s）：%s", self.root, exc)
            return []

        models: list[AvatarModel] = []
        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                raw = self._reader.read_json(entry / MODEL_META_FILE)
            except Exception:
                continue  # 没有 meta 的目录不是模型（可能只是残留目录），静默跳过
            try:
                models.append(AvatarModel.from_dict(raw))
            except Exception as exc:
                logger.warning("模型元数据损坏（%s）：%s", entry.name, exc)

        models.sort(key=lambda model: model.created_at, reverse=True)
        return models


def _describe(model) -> ModelDescriptor:
    """`AvatarModel` → 中性描述符。"""
    return ModelDescriptor(
        id=model.id,
        display_name=model.name,
        kind=model.kind.value,
        source=LocalLibrarySource.name,
        installed=True,
        entry=model.entry or "",
        description=_summary(model),
        extra={
            "createdAt": model.created_at,
            "expressions": list(model.expressions),
            "images": list(model.images),
        },
    )


def _summary(model) -> str:
    if model.kind.value == "live2d":
        count = len(model.expressions)
        suffix = f"，{count} 个表情" if count else ""
        return f"Live2D 模型{suffix}"
    return f"静态立绘（{len(model.images)} 张图片）"
