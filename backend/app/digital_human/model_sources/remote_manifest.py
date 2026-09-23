"""清单来源：从一份 JSON 里读取「有哪些模型可以获取」。

**只读清单，不代下载**（本项目的取舍）：

- 模型**不可自由分发**（授权与体积原因，见 `docs/license-compliance.md`），
  代下载会把「用户自己的选择」变成「本项目在分发第三方模型」；
- 下载 + 解包是**网络写操作**，会把一个只读的展示功能变成攻击面。

所以本来源只回答「有哪些、谁做的、什么条款、去哪拿」，实际获取由用户自行完成后
走既有的上传流程（`POST /media/avatar/models`）。

**只支持本地路径与 `file://`，不支持 http(s)**：拉远程 JSON 属于网络访问，
而插件权限模型目前只对文件系统有强制校验（`PluginContext` 的受控读 API），
网络没有对应闸门——声明 `network.hosts: []` 却真去发请求，等于把声明写成空话。
需要社区清单时，把 JSON 下到本地再配路径即可；评审环境通常也没有外网。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app.digital_human.model_sources.base import ModelDescriptor, ModelSource

logger = logging.getLogger(__name__)

#: 清单格式版本（将来结构变化时用它做兼容分支）
MANIFEST_VERSION = 1

#: 识别为「模型」的 kind 取值
_KINDS = frozenset({"live2d", "images"})


class RemoteManifestSource(ModelSource):
    """外部清单（本地文件 / `file://` URL）。"""

    name = "remote-manifest"

    def __init__(self, location: str = "", *, reader: Any = None) -> None:
        self.location = (location or "").strip()
        self._reader = reader

    def available(self) -> bool:
        return bool(self.location)

    def list_models(self) -> list[ModelDescriptor]:
        payload = self._load()
        if payload is None:
            return []

        entries = payload.get("models")
        if not isinstance(entries, list):
            logger.warning("模型清单缺少 models 数组：%s", self.location)
            return []

        found: list[ModelDescriptor] = []
        for index, item in enumerate(entries):
            descriptor = _parse_entry(item, index, source=self.name)
            if descriptor is not None:
                found.append(descriptor)
        return found

    def describe(self) -> str:
        return f"模型清单（{self.location}）" if self.location else "未配置模型清单"

    # ---------------- 读取 ----------------

    def _resolve_path(self) -> Path | None:
        location = self.location
        if location.startswith("file://"):
            parsed = urlparse(location)
            # **必须 unquote**：`Path.as_uri()` 会把空格 / 中文编成 %20 / %E4…，
            # 而 Path 不会自动解码——路径带空格的用户（Windows 上很常见）会读不到文件。
            # Windows 的 file:///C:/x 会被解析为 /C:/x，去掉多余的前导斜杠。
            raw = unquote(parsed.path)
            if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
                raw = raw[1:]
            return Path(raw)
        if "://" in location:
            logger.warning(
                "模型清单不支持网络地址（%s）：请先下载到本地再配置路径。"
                "原因见 remote_manifest.py 的模块说明",
                location,
            )
            return None
        return Path(location)

    def _load(self) -> dict | None:
        path = self._resolve_path()
        if path is None:
            return None

        if self._reader is not None:
            # 插件路径：过宿主的读权限白名单。拿不到句柄与文件不存在一视同仁（都只警告）
            try:
                payload = self._reader.read_json(path)
            except PermissionError as exc:
                logger.warning("模型清单不在插件读权限内（%s）：%s", path, exc)
                return None
            except Exception as exc:
                logger.warning("模型清单读取失败（%s）：%s", path, exc)
                return None
        else:
            if not path.is_file():
                logger.warning("模型清单不存在：%s", path)
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # 清单损坏不该让整张清单空掉
                logger.warning("模型清单解析失败（%s）：%s", path, exc)
                return None

        if not isinstance(payload, dict):
            logger.warning("模型清单顶层应为对象：%s", path)
            return None

        version = payload.get("version")
        if version not in (None, MANIFEST_VERSION):
            logger.warning(
                "模型清单版本为 %s，本版本仅理解 %d（仍尝试按当前结构读取）",
                version,
                MANIFEST_VERSION,
            )
        return payload


def _parse_entry(raw: object, index: int, *, source: str) -> ModelDescriptor | None:
    """把清单里的一条转成描述符；缺关键字段时跳过该条。"""
    if not isinstance(raw, dict):
        return None

    model_id = str(raw.get("id") or "").strip()
    if not model_id:
        logger.warning("模型清单第 %d 项缺少 id，已跳过", index + 1)
        return None

    kind = str(raw.get("kind") or "live2d").strip().lower()
    if kind not in _KINDS:
        logger.warning("模型 %s 的 kind 未知（%s），按 live2d 处理", model_id, kind)
        kind = "live2d"

    known = {
        "id", "name", "kind", "description", "preview",
        "author", "license", "homepage", "download",
    }
    return ModelDescriptor(
        id=model_id,
        display_name=str(raw.get("name") or model_id).strip(),
        kind=kind,
        source=source,
        installed=False,  # 由聚合层在合并时按「本机是否已有」改写
        description=str(raw.get("description") or "").strip(),
        preview_url=str(raw.get("preview") or "").strip(),
        author=str(raw.get("author") or "").strip(),
        license=str(raw.get("license") or "").strip(),
        homepage=str(raw.get("homepage") or raw.get("download") or "").strip(),
        extra={key: value for key, value in raw.items() if key not in known},
    )
