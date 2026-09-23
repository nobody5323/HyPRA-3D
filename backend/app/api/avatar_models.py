"""数字人模型库接口（上传 / 清单 / 指定情绪映射 / 删除 / 取文件）。

设计要点：
- **上传即可用**——Live2D 模型包解压后会自动补全 `model3.json` 的
  Expressions / Motions 引用并生成约定入口 `pet.model3.json`；
- **静态立绘不猜情绪**——后端只负责存图片，`情绪 → 图片` 由前端逐个指定
  （存进该模型的 `expressionMap`，这样换浏览器也不会丢）；
- **文件统一走本路由**——前端渲染器把模型 URL 指向
  `GET /media/avatar/models/{id}/files/{path}`，不需要知道磁盘结构。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.digital_human.model_store import (
    AvatarModelKind,
    AvatarModelStore,
    ModelStoreError,
)

router = APIRouter(prefix="/media/avatar/models", tags=["avatar-models"])


def get_store() -> AvatarModelStore:
    """按配置构建模型库（目录可配置，便于测试隔离）。"""
    return AvatarModelStore(Path(get_settings().avatar_models_dir))


def _bad_request(error: ModelStoreError) -> HTTPException:
    """模型库的业务错误 → 400（附带可读原因，前端直接展示）。"""
    return HTTPException(status_code=400, detail=str(error))


class AvatarModelPatch(BaseModel):
    """可更新的字段（情绪映射由前端逐个指定）。"""

    name: str | None = None
    expression_map: dict[str, str] | None = Field(default=None, alias="expressionMap")

    model_config = {"populate_by_name": True}


@router.get("")
def list_models() -> dict:
    """列出全部模型（新的在前）。"""
    store = get_store()
    return {"models": [model.to_dict() for model in store.list_models()]}


@router.post("", status_code=201)
async def create_model(
    kind: Annotated[str, Form(description="模型类型：live2d | images")],
    files: Annotated[list[UploadFile], File(description="live2d 传单个 zip；images 传若干图片")],
    name: Annotated[str, Form(description="展示名")] = "",
) -> dict:
    """上传一个模型。

    - `live2d`：只取第一个文件当模型包（本项目的 Live2D 模型都是整包 zip）；
    - `images`：取全部图片，按上传顺序编号保存（前端再按序号指定情绪）。
    """
    store = get_store()
    if not files:
        raise HTTPException(status_code=422, detail="没有上传文件")

    try:
        model_kind = AvatarModelKind(kind)
    except ValueError:
        raise HTTPException(
            status_code=422, detail=f"未知类型：{kind!r}（可选 live2d | images）"
        ) from None

    try:
        if model_kind is AvatarModelKind.LIVE2D:
            archive = await files[0].read()
            model = store.create_from_zip(name or Path(files[0].filename or "").stem, archive)
        else:
            payloads = [(upload.filename or "image", await upload.read()) for upload in files]
            model = store.create_from_images(name, payloads)
    except ModelStoreError as error:
        raise _bad_request(error) from error

    return model.to_dict()


@router.get("/{model_id}")
def get_model(model_id: str) -> dict:
    """取单个模型详情（含表情清单 / 图片清单 / 情绪映射）。"""
    model = get_store().get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"模型不存在：{model_id}")
    return model.to_dict()


@router.patch("/{model_id}")
def patch_model(model_id: str, payload: AvatarModelPatch) -> dict:
    """更新名称 / 情绪映射。"""
    try:
        model = get_store().update_model(
            model_id, name=payload.name, expression_map=payload.expression_map
        )
    except ModelStoreError as error:
        raise _bad_request(error) from error
    return model.to_dict()


@router.delete("/{model_id}")
def delete_model(model_id: str) -> dict:
    """删除模型及其全部文件。"""
    try:
        deleted = get_store().delete_model(model_id)
    except ModelStoreError as error:
        raise _bad_request(error) from error
    if not deleted:
        raise HTTPException(status_code=404, detail=f"模型不存在：{model_id}")
    return {"deleted": True, "id": model_id}


@router.get("/{model_id}/files/{file_path:path}")
def get_model_file(model_id: str, file_path: str) -> FileResponse:
    """读取模型内的文件（入口 model3.json、moc3、贴图、表情图…）。"""
    try:
        target = get_store().resolve_file(model_id, file_path)
    except ModelStoreError as error:
        # 路径越界与文件不存在都按 404 处理：不向调用方泄露目录结构
        raise HTTPException(status_code=404, detail=str(error)) from error

    # 不缓存：模型会被重新上传/替换，缓存反而让前端看到旧文件
    return FileResponse(target, headers={"Cache-Control": "no-store"})
