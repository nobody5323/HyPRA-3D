"""模型来源的中性契约：插件与宿主之间的数据边界（`AGENTS.md §9.4`）。

与 `app/plugins/contracts.py` 的关系：那份契约描述的是「设定条目 / 角色 / 会话」
（酒馆接入的形状）；这份描述的是「数字人模型从哪来」。两者都属 `datasource` 能力，
但产出的中性对象形状完全不同——硬塞进同一个快照类只会让两边都别扭。

**为什么需要它**：模型「有哪些可以用」原本硬编码在前端常量表
（`frontend/lib/live2d/model-assets.ts`）里，加一个模型要改前端代码。
抽成来源之后，本机模型库、清单文件、将来的内置示例都是同一接口的实现，
前端只消费统一清单。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ModelDescriptor:
    """一个可用的数字人模型（**中性对象**：不含任何存储路径细节）。

    宿主负责把它映射成前端展示的形状；来源侧不知道前端长什么样。
    """

    id: str
    display_name: str
    #: `live2d` | `images`
    kind: str = "live2d"
    #: 来源 id（`local-library` / `remote-manifest` …）
    source: str = ""
    #: 是否已在本机模型库里。已装可直接选用；未装只是「可获取」
    installed: bool = False
    #: 已安装时：Live2D 入口相对路径（如 `pet.model3.json`）
    entry: str = ""
    description: str = ""
    #: 预览图地址（清单提供；本机库可留空）
    preview_url: str = ""
    author: str = ""
    #: 授权说明 —— **合规必需**：模型不可自由分发，用户必须知道来源与条款
    license: str = ""
    #: 获取地址（本来源**只给链接，不代下载**）
    homepage: str = ""
    #: 来源特有字段透传（宿主不解释）
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "displayName": self.display_name,
            "kind": self.kind,
            "source": self.source,
            "installed": self.installed,
            "entry": self.entry,
            "description": self.description,
            "previewUrl": self.preview_url,
            "author": self.author,
            "license": self.license,
            "homepage": self.homepage,
            "extra": dict(self.extra),
        }


class ModelSource(ABC):
    """模型来源接口（一种「从哪找模型」的方式一个实现）。"""

    #: 来源 id（配置与 UI 展示都用它）
    name: str = ""

    def available(self) -> bool:
        """来源是否就绪（没配置好时返回 False，而不是抛错）。"""
        return True

    @abstractmethod
    def list_models(self) -> list[ModelDescriptor]:
        """列出本来源提供的模型。

        读取失败时返回**空列表**并记日志：一个来源坏掉不该让整张清单空掉。
        """

    def describe(self) -> str:
        """人类可读的来源状态（供 UI 与调试）。"""
        return self.name
