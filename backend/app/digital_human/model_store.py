"""数字人模型库：上传的 Live2D / 静态立绘的落盘、校验与清单。

为什么存后端而不是前端 `public/`：
- 上传属于**运行期**行为，而 `frontend/public/` 是构建产物（docker 镜像里只读）；
- 前后端解耦：前端只通过 HTTP 取模型文件，不需要知道服务器目录结构。

目录结构（`backend/data/avatar_models/`，已被 .gitignore 覆盖）::

    <model_id>/
        model.json      元数据（名称 / 类型 / 表情清单 / 情绪映射）
        files/          模型文件本体（Live2D 全量文件，或若干张立绘）

两类模型：
- ``live2d``：上传 `.zip`（模型包）。会自动补全 `model3.json` 里缺失的
  Expressions / Motions 引用（VTube Studio 导出的模型通病），
  并生成本项目约定入口 ``pet.model3.json``。
- ``images``：上传若干张图片。**不在后端约定文件名与情绪的对应关系**，
  而是把「情绪 → 文件名」交给前端逐个指定（存在 `expressionMap` 里），
  因为只有用户自己知道哪张图是什么表情。
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath

MODEL_META_FILE = "model.json"
FILES_DIR = "files"

#: 本项目约定的 Live2D 入口文件名（与前端 model-assets.ts 保持一致）
LIVE2D_ENTRY = "pet.model3.json"

EXPRESSION_SUFFIX = ".exp3.json"
MOTION_SUFFIX = ".motion3.json"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}

#: 单个模型包上限（Live2D 模型带贴图常见几十 MB）
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
#: 单张图片上限
MAX_IMAGE_BYTES = 20 * 1024 * 1024
#: 元数据里保存的情绪 id（与后端 EmotionLabel 的 8 类一致）
EMOTION_IDS = ("happy", "calm", "sad", "anxious", "tired", "angry", "surprised", "neutral")

#: 构图校准（layout）的缩放区间。
#:
#: 越界一律**报错**而不是静默夹紧：夹紧会让「我明明拖到了却没生效」这种问题
#: 只能靠读代码才查得出来。
LAYOUT_SCALE_MIN = 0.1
LAYOUT_SCALE_MAX = 5.0
#: 位移上限（px）：足够把任何比例的模型挪进画布，又不至于挪到天外
LAYOUT_OFFSET_LIMIT = 2000
#: anchor 只允许 CSS `transform-origin` 的常见写法（`bottom center` / `50% 100%`）
_ANCHOR_PATTERN = re.compile(r"^[A-Za-z0-9 %.\-]{1,40}$")

#: 「当前选用的模型」落盘文件名。
#:
#: 放在模型库根目录下：`list_models` 只遍历**目录**，所以这个文件不会被当成模型。
#: 之所以放后端而不是前端 localStorage：Web 端与桌面端的 origin 不同
#: （`localhost:3000` vs `127.0.0.1:34567`），localStorage 天然不共享，
#: 而「现在用哪个模型」必须是两端一致的事实。
SELECTION_FILE = "_selection.json"


class AvatarModelKind(str, Enum):
    """模型类型。"""

    LIVE2D = "live2d"
    IMAGES = "images"


class ModelStoreError(Exception):
    """模型库操作失败（调用方转成 HTTP 4xx）。"""


@dataclass
class AvatarModel:
    """一个模型的元数据。

    - ``expressions``：Live2D 专用，模型自带的表情名清单（供前端配置情绪映射）；
    - ``images``：静态立绘专用，上传的图片文件名清单；
    - ``expression_map``：静态立绘专用，``情绪 id → 图片文件名``（**由前端逐个指定**）；
    - ``layout``：构图校准（缩放 / 位移 / 锚点）。不同模型的画布比例差别很大，
      同一套默认构图必然有的显示不全，所以校准参数随模型保存；
      ``None`` = 未校准（渲染层用默认构图）。
    """

    id: str
    name: str
    kind: AvatarModelKind
    created_at: str
    updated_at: str
    entry: str | None = None
    expressions: list[str] = None  # type: ignore[assignment]
    images: list[str] = None  # type: ignore[assignment]
    expression_map: dict[str, str] = None  # type: ignore[assignment]
    layout: dict | None = None
    meta: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.expressions = list(self.expressions or [])
        self.images = list(self.images or [])
        self.expression_map = dict(self.expression_map or {})
        self.layout = dict(self.layout) if isinstance(self.layout, dict) else None
        self.meta = dict(self.meta or {})

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "entry": self.entry,
            "expressions": list(self.expressions),
            "images": list(self.images),
            "expressionMap": dict(self.expression_map),
            "layout": dict(self.layout) if self.layout else None,
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "AvatarModel":
        return cls(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            kind=AvatarModelKind(raw.get("kind", AvatarModelKind.IMAGES.value)),
            created_at=str(raw.get("createdAt") or _now()),
            updated_at=str(raw.get("updatedAt") or _now()),
            entry=raw.get("entry"),
            expressions=raw.get("expressions") or [],
            images=raw.get("images") or [],
            expression_map=raw.get("expressionMap") or {},
            layout=raw.get("layout"),
            meta=raw.get("meta") or {},
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_number(value: object, field: str) -> float:
    """把 JSON 里的数值字段转成 float。

    显式排除 bool：Python 里 `True` 也是 `int`，不拦的话 `scale: true`
    会被当成 1 悄悄存下来。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelStoreError(f"{field} 必须是数字")
    return float(value)


def sanitize_layout(raw: dict | None) -> dict | None:
    """校验并规整构图校准参数；返回 None 表示「未校准」（渲染层用默认构图）。

    只认四个已知键（``scale`` / ``offsetX`` / ``offsetY`` / ``anchor``），
    未知键**静默忽略**——这样前端将来加参数时，旧后端不会因为多一个字段就报 400。
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ModelStoreError("layout 必须是对象")

    cleaned: dict[str, object] = {}

    if raw.get("scale") is not None:
        scale = _as_number(raw.get("scale"), "scale")
        if not LAYOUT_SCALE_MIN <= scale <= LAYOUT_SCALE_MAX:
            raise ModelStoreError(
                f"scale 需在 {LAYOUT_SCALE_MIN} ~ {LAYOUT_SCALE_MAX} 之间（收到 {scale}）"
            )
        cleaned["scale"] = round(scale, 3)

    for key in ("offsetX", "offsetY"):
        if raw.get(key) is None:
            continue
        offset = _as_number(raw.get(key), key)
        if abs(offset) > LAYOUT_OFFSET_LIMIT:
            raise ModelStoreError(
                f"{key} 需在 ±{LAYOUT_OFFSET_LIMIT}px 之间（收到 {offset}）"
            )
        cleaned[key] = int(round(offset))

    if raw.get("anchor") is not None:
        anchor = str(raw.get("anchor")).strip()
        if not _ANCHOR_PATTERN.match(anchor):
            raise ModelStoreError(f"anchor 不合法：{anchor!r}（形如 bottom center）")
        cleaned["anchor"] = anchor

    return cleaned or None


def decode_zip_name(info: zipfile.ZipInfo) -> str:
    """还原压缩包里被非 UTF-8 标志标记的中文文件名。

    中文 Windows 打的包常用 GBK 存名，Python 会先按 cp437 解出乱码；
    不修正的话，模型里那些中文表情文件名（`开心兴奋.exp3.json`）会全部对不上。
    """
    name = info.filename
    if info.flag_bits & 0x800:  # 已声明 UTF-8
        return name
    for codec in ("utf-8", "gbk"):
        try:
            return name.encode("cp437").decode(codec)
        except (UnicodeError, LookupError):
            continue
    return name


def safe_relative_path(name: str) -> PurePosixPath:
    """校验压缩包内 / 上传的相对路径（防路径穿越、防盘符、防 URL 转义）。"""
    if not name or name.endswith("/"):
        raise ModelStoreError(f"路径无效：{name!r}")
    if re.search(r"[:\\%]", name):  # 盘符 / 反斜杠 / 百分号转义
        raise ModelStoreError(f"路径包含非法字符：{name}")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in ("..", ".") for part in relative.parts):
        raise ModelStoreError(f"路径越界：{name}")
    return relative


def _guard_inside(root: Path, candidate: Path) -> Path:
    """确保目标路径仍在 root 之内（resolve 之后再判，符号链接也拦得住）。"""
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ModelStoreError("路径越界")
    return resolved


def augment_model3(source: dict, files: list[str]) -> dict:
    """补全 `model3.json` 缺失的 Expressions / Motions 引用。

    VTube Studio 导出的模型，`FileReferences` 里常常**只有** Moc/Textures/Physics，
    表情与动作交给 VTS 自己管；而官方 Cubism SDK 只认这里的引用——
    结果就是「模型能显示，却一个表情也切不了」。已存在的引用一律不动。
    """
    references = source.setdefault("FileReferences", {})

    if not references.get("Expressions"):
        references["Expressions"] = sorted(
            (
                {"Name": PurePosixPath(f).name[: -len(EXPRESSION_SUFFIX)], "File": f}
                for f in files
                if f.endswith(EXPRESSION_SUFFIX)
            ),
            key=lambda item: item["Name"],
        )

    if not references.get("Motions"):
        motions: dict[str, list[dict]] = {}
        for path in files:
            if not path.endswith(MOTION_SUFFIX):
                continue
            base = PurePosixPath(path).name[: -len(MOTION_SUFFIX)]
            group = "Idle" if "idle" in base.lower() else base
            motions.setdefault(group, []).append(
                {"File": path, "FadeInTime": 0.5, "FadeOutTime": 0.5}
            )
        if motions:
            # Idle 排最前，便于人读
            ordered = {"Idle": motions["Idle"]} if "Idle" in motions else {}
            for key in sorted(k for k in motions if k != "Idle"):
                ordered[key] = motions[key]
            references["Motions"] = ordered

    # 口型参数组：官方 SDK 的部分辅助功能会读它（本项目桥直接用参数名，不依赖）
    groups = source.setdefault("Groups", [])
    if not any(group.get("Name") == "LipSync" for group in groups):
        groups.append({"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"]})

    return source


class AvatarModelStore:
    """模型库的文件系统实现（无数据库：模型本来就是一堆文件）。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # ---------------------------------------------------------------
    # 读
    # ---------------------------------------------------------------
    def list_models(self) -> list[AvatarModel]:
        if not self.root.is_dir():
            return []
        models: list[AvatarModel] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir():
                continue
            model = self._read_meta(entry.name)
            if model is not None:
                models.append(model)
        # 新的在前（上传后立刻能在列表里看到）
        return sorted(models, key=lambda model: model.created_at, reverse=True)

    def get_model(self, model_id: str) -> AvatarModel | None:
        return self._read_meta(model_id)

    def model_dir(self, model_id: str) -> Path:
        _guard_inside(self.root, self.root / model_id)
        directory = self.root / model_id
        if not directory.is_dir():
            raise ModelStoreError(f"模型不存在：{model_id}")
        return directory

    def resolve_file(self, model_id: str, relative: str) -> Path:
        """把模型内相对路径解析成磁盘路径（含路径穿越防护）。"""
        files_root = self.model_dir(model_id) / FILES_DIR
        if not files_root.is_dir():
            raise ModelStoreError(f"模型没有文件目录：{model_id}")
        target = _guard_inside(files_root, files_root / safe_relative_path(relative))
        if not target.is_file():
            raise ModelStoreError(f"文件不存在：{relative}")
        return target

    # ---------------------------------------------------------------
    # 写
    # ---------------------------------------------------------------
    def create_from_zip(self, name: str, archive: bytes) -> AvatarModel:
        """从 Live2D 模型包（zip）创建模型。"""
        if not archive:
            raise ModelStoreError("上传内容为空")
        if len(archive) > MAX_ARCHIVE_BYTES:
            raise ModelStoreError(f"模型包超过上限（{MAX_ARCHIVE_BYTES // 1024 // 1024}MB）")

        model_id = self._new_id()
        model_dir = self.root / model_id
        files_dir = model_dir / FILES_DIR
        files_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._extract_zip(archive, files_dir)
            entry = self._prepare_live2d(files_dir)
        except ModelStoreError:
            shutil.rmtree(model_dir, ignore_errors=True)
            raise
        except Exception as error:  # 解压/解析意外失败同样清理，不留半个模型
            shutil.rmtree(model_dir, ignore_errors=True)
            raise ModelStoreError(f"模型包处理失败：{error}") from error

        model = AvatarModel(
            id=model_id,
            name=name.strip() or "未命名模型",
            kind=AvatarModelKind.LIVE2D,
            created_at=_now(),
            updated_at=_now(),
            # `entry` 是**相对 files/ 目录**的入口（zip 里可能套了一层文件夹）
            entry=entry,
            expressions=self._read_expressions(files_dir / entry),
            meta={"fileCount": sum(1 for path in files_dir.rglob("*") if path.is_file())},
        )
        self._write_meta(model)
        return model

    def create_from_images(self, name: str, images: list[tuple[str, bytes]]) -> AvatarModel:
        """从若干张图片创建静态立绘模型（情绪映射留空，由前端指定）。"""
        if not images:
            raise ModelStoreError("没有可用的图片")

        model_id = self._new_id()
        model_dir = self.root / model_id
        files_dir = model_dir / FILES_DIR
        files_dir.mkdir(parents=True, exist_ok=True)

        saved: list[str] = []
        try:
            for index, (filename, payload) in enumerate(images):
                suffix = Path(filename).suffix.lower()
                if suffix not in IMAGE_SUFFIXES:
                    raise ModelStoreError(f"不支持的图片格式：{filename}")
                if len(payload) > MAX_IMAGE_BYTES:
                    raise ModelStoreError(f"图片过大：{filename}")
                # 重命名成序号 + 原后缀：避免中文名/重名/怪字符带来的问题，
                # 同时保留顺序（前端按序展示待指定）
                safe_name = f"image_{index:02d}{suffix}"
                (files_dir / safe_name).write_bytes(payload)
                saved.append(safe_name)
        except ModelStoreError:
            shutil.rmtree(model_dir, ignore_errors=True)
            raise

        model = AvatarModel(
            id=model_id,
            name=name.strip() or "未命名立绘",
            kind=AvatarModelKind.IMAGES,
            created_at=_now(),
            updated_at=_now(),
            images=saved,
            meta={"fileCount": len(saved)},
        )
        self._write_meta(model)
        return model

    def update_model(
        self,
        model_id: str,
        *,
        name: str | None = None,
        expression_map: dict[str, str] | None = None,
        layout: dict | None = None,
        update_layout: bool = False,
    ) -> AvatarModel:
        """更新名称 / 情绪映射 / 构图校准（映射只允许指向该模型里真实存在的图片）。

        ``layout`` 是否生效由 ``update_layout`` 决定，而不是看 ``layout is None``：
        「没传 layout」与「传 null 清除校准」是两件不同的事，混在一起的话，
        一次改名就会顺手把用户调好的构图抹掉。
        """
        model = self.get_model(model_id)
        if model is None:
            raise ModelStoreError(f"模型不存在：{model_id}")

        if name is not None and name.strip():
            model.name = name.strip()

        if update_layout:
            model.layout = sanitize_layout(layout)

        if expression_map is not None:
            if model.kind is not AvatarModelKind.IMAGES:
                raise ModelStoreError("只有静态立绘模型需要指定情绪映射")
            cleaned: dict[str, str] = {}
            for emotion, filename in expression_map.items():
                if emotion not in EMOTION_IDS:
                    raise ModelStoreError(f"未知情绪：{emotion}")
                if not filename:
                    continue  # 空值 = 该情绪不指定（前端用兜底图）
                safe_relative_path(filename)
                if filename not in model.images:
                    raise ModelStoreError(f"图片不在该模型中：{filename}")
                cleaned[emotion] = filename
            model.expression_map = cleaned

        model.updated_at = _now()
        self._write_meta(model)
        return model

    def delete_model(self, model_id: str) -> bool:
        directory = self.root / model_id
        _guard_inside(self.root, directory)
        if not directory.is_dir():
            return False
        # 删除前先读选择：删完之后模型已不存在，再读就会回落成空串而看不出原值
        current = self.get_selection()
        shutil.rmtree(directory)
        if current == model_id:
            # 走 set_selection("") 而不是直接写文件：空值不触发存在性校验
            self.set_selection("")
        return True

    def get_selection(self) -> str:
        """当前选用的模型 id（空串 = 内置模型）。

        指向的模型已被删除时**顺手回落**成空串并落盘：这个值会被 Web 端与桌面端
        共同读取，留一个悬空 id 只会让两边各自写一遍「找不到就回落」的逻辑。
        """
        path = self.root / SELECTION_FILE
        if not path.is_file():
            return ""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 文件被写坏不该让整个模型库不可用，按「未选择」处理
            return ""

        model_id = str((raw or {}).get("modelId") or "").strip()
        if not model_id:
            return ""
        if self.get_model(model_id) is None:
            self.set_selection("")
            return ""
        return model_id

    def set_selection(self, model_id: str | None) -> str:
        """写入当前选用的模型（空 / None = 回到内置模型）。"""
        target = str(model_id or "").strip()
        if target and self.get_model(target) is None:
            raise ModelStoreError(f"模型不存在：{target}")

        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / SELECTION_FILE).write_text(
            json.dumps({"modelId": target}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    # ---------------------------------------------------------------
    # 内部
    # ---------------------------------------------------------------
    def _new_id(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        for _ in range(10):
            candidate = f"m_{uuid.uuid4().hex[:10]}"
            if not (self.root / candidate).exists():
                return candidate
        raise ModelStoreError("无法分配模型 id")

    def _read_meta(self, model_id: str) -> AvatarModel | None:
        meta_path = self.root / model_id / MODEL_META_FILE
        if not meta_path.is_file():
            return None
        try:
            return AvatarModel.from_dict(json.loads(meta_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def _write_meta(self, model: AvatarModel) -> None:
        model_dir = self.root / model.id
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / MODEL_META_FILE).write_text(
            json.dumps(model.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _extract_zip(self, archive: bytes, target_dir: Path) -> None:
        import io

        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            for info in handle.infolist():
                if info.is_dir():
                    continue
                name = decode_zip_name(info)
                relative = safe_relative_path(name)
                destination = _guard_inside(target_dir, target_dir.joinpath(*relative.parts))
                destination.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(info) as source, open(destination, "wb") as sink:
                    shutil.copyfileobj(source, sink)

    def _prepare_live2d(self, files_dir: Path) -> str:
        """定位 model3.json → 补全引用 → 写出约定的入口文件。

        返回值是**相对 `files/`** 的入口路径（前端用 `{base}/files/{entry}` 取它）。
        压缩包里多套一层文件夹是常见情形，所以不能假设入口就在根目录。
        """
        candidates = sorted(
            path
            for path in files_dir.rglob("*.model3.json")
            if path.name != LIVE2D_ENTRY
        )
        if not candidates:
            raise ModelStoreError("模型包里没有 *.model3.json（不是 Cubism 模型？）")
        if len(candidates) > 1:
            # 取最外层（层级最浅）的那个，其余保持原样不影响加载
            candidates.sort(key=lambda path: len(path.relative_to(files_dir).parts))

        source_path = candidates[0]
        base = source_path.parent
        try:
            source = json.loads(source_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ModelStoreError(f"model3.json 解析失败：{error}") from error

        # 相对入口文件的路径（模型内部引用都是相对 model3.json 的）
        files = [
            path.relative_to(base).as_posix()
            for path in sorted(base.rglob("*"))
            if path.is_file()
        ]
        augmented = augment_model3(source, files)
        entry_path = base / LIVE2D_ENTRY
        entry_path.write_text(
            json.dumps(augmented, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        return entry_path.relative_to(files_dir).as_posix()

    def _read_expressions(self, entry_path: Path) -> list[str]:
        if not entry_path.is_file():
            return []
        try:
            data = json.loads(entry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        references = data.get("FileReferences") or {}
        return [
            str(item.get("Name"))
            for item in references.get("Expressions") or []
            if item.get("Name")
        ]
