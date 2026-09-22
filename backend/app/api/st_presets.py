"""SillyTavern 预设兼容的路由与存储门面（契约见 docs/st-preset-compat.md）。

七个端点（前缀 /chat/st-presets）：

    GET    ""                已导入预设清单（**不含任何提示词正文**）
    POST   "/import"         导入（解析失败不落盘）
    GET    "/{preset_id}"    详情（归一化结构 + 条目清单 + 未生效特性）
    PATCH  "/{preset_id}"    写覆盖层（原始文件始终只读）
    POST   "/{preset_id}/reset"    清空覆盖层，回到导入时
    GET    "/{preset_id}/export"   导出为酒馆兼容 JSON
    DELETE "/{preset_id}"    删除

合规（AGENTS.md §6）：导入的预设正文属**用户本地数据**——存在 `ST_PRESETS_DIR`
（默认 backend/data/presets，已被 .gitignore 覆盖），本模块只做解析与渲染，
不进仓库、不随发行物分发。本项目不内置任何 SillyTavern 或社区预设的提示词原文。
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import get_settings
from app.prompts.st_compat import (
    RUNTIME_FILLED_MARKERS,
    VALID_ROLES,
    ParsedPreset,
    StPresetStore,
    marker_source_label,
)

router = APIRouter(prefix="/chat/st-presets", tags=["st-presets"])

#: 进程内单例（依赖懒加载；测试可经 set_st_preset_store 注入）
_st_preset_store: StPresetStore | None = None


def get_st_preset_store() -> StPresetStore:
    """懒加载 ST 预设存储（导入的预设属**用户本地数据**，不入库）。

    目录由 `ST_PRESETS_DIR` 配置（默认 backend/data/presets，已被 .gitignore 覆盖）。
    存储不可用时只影响预设功能，不影响内置分层模式的对话。
    """
    global _st_preset_store
    if _st_preset_store is None:
        _st_preset_store = StPresetStore(get_settings().st_presets_dir)
    return _st_preset_store


def set_st_preset_store(store: StPresetStore | None) -> None:
    """替换/重置 ST 预设存储（测试注入用）。"""
    global _st_preset_store
    _st_preset_store = store




# ---------- SillyTavern 预设（导入 / 编辑 / 导出）----------

#: 能真正透传给 provider 的采样参数
_ST_APPLIED_SAMPLING = (
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "max_tokens",
)

#: 仅保存与展示的扩展采样参数（当前 provider 不透传，界面需标注“忽略”，
#: 不静默假装生效；详见 docs/st-preset-compat.md §4）
_ST_EXTENDED_SAMPLING = ("top_k", "top_a", "min_p", "repetition_penalty", "seed", "n")

#: 组装开关的合法取值（枚举型，非法值必须拒收）
_ASSEMBLY_VALIDATORS = {
    "use_sysprompt": lambda value: isinstance(value, bool),
    "squash_system_messages": lambda value: isinstance(value, bool),
    "names_behavior": lambda value: (
        isinstance(value, int) and not isinstance(value, bool) and value in (0, 1, 2)
    ),
}


def _validate_st_patch(patch: dict) -> None:
    """写覆盖层前校验枚举型字段。

    为什么必须拒收而不是“存下再忽略”：覆盖层是**深合并**的，存下一个非法值会把
    上一次的合法值顶掉，结果反而回退到预设原值（用户会以为“改坏了”）。
    """
    assembly = patch.get("assembly")
    if isinstance(assembly, dict):
        for key, value in assembly.items():
            validator = _ASSEMBLY_VALIDATORS.get(key)
            if validator is None or not validator(value):
                raise HTTPException(
                    status_code=400, detail=f"组装开关「{key}」取值非法：{value!r}"
                )

    items = patch.get("prompts")
    if isinstance(items, dict):
        for identifier, fields in items.items():
            if not isinstance(fields, dict):
                continue
            role = fields.get("role")
            if role is not None and role not in VALID_ROLES:
                raise HTTPException(
                    status_code=400, detail=f"条目「{identifier}」的角色非法：{role!r}"
                )


class StPresetImportRequest(BaseModel):
    """导入一份 ST 预设（内容来自用户本地文件）。"""

    content: str | dict = Field(description="预设 JSON 文本或对象（用户本地文件内容）")
    source_file: str = Field(default="", description="原始文件名（用于推导预设 id）")
    preset_id: str | None = Field(default=None, description="显式指定 id；缺省由文件名推导")
    overwrite: bool = Field(
        default=False, description="允许覆盖同名预设（会作废其界面编辑）"
    )


class StPresetPatchRequest(BaseModel):
    """覆盖层补丁（字段级深合并，原始文件不改动，见契约文档 §9）。

    值为 `null` 表示**删除该覆盖项**（界面上的「恢复预设原值」）；
    未出现的字段表示不修改 —— 因此补丁用 `exclude_unset` 取，而不是 `exclude_none`
    （后者会把嵌套的 null 一并剔除，导致「恢复原值」永远不生效）。
    """

    sampling: dict[str, float | int | None] | None = None
    prompts: dict[str, dict[str, object]] | None = None
    prompt_order: list[str] | None = None
    memory_injection: dict[str, object] | None = None
    assembly: dict[str, object] | None = None


def _load_st_preset(preset_id: str) -> tuple[StPresetStore, ParsedPreset]:
    """取存储与已解析（含覆盖层）的预设；不存在时 404。"""
    store = get_st_preset_store()
    try:
        return store, store.load(preset_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"预设不存在：{preset_id}") from exc


def _st_preset_detail(store: StPresetStore, preset_id: str, parsed: ParsedPreset) -> dict:
    """预设详情：归一化结果 + 界面编辑所需的全部结构信息。

    正文会返回给发起导入的本地界面（用户自己的内容），但**不入库、不进日志**。
    """
    preset = parsed.preset
    sampling = preset.sampling
    return {
        "id": preset_id,
        "sampling": {k: v for k, v in sampling.items() if k in _ST_APPLIED_SAMPLING},
        "extended_sampling": {
            k: v for k, v in sampling.items() if k in _ST_EXTENDED_SAMPLING
        },
        "assembly": {
            "use_sysprompt": preset.use_sysprompt,
            "squash_system_messages": preset.squash_system_messages,
            "names_behavior": preset.names_behavior,
        },
        "order": [
            {"identifier": entry.identifier, "enabled": entry.enabled}
            for entry in parsed.order
        ],
        "prompts": [
            {
                "identifier": item.identifier,
                "name": item.display_name,
                "role": item.role,
                "content": item.content,
                "marker": item.is_marker,
                "system_prompt": item.system_prompt,
                "forbid_overrides": item.forbid_overrides,
                "injection_position": item.injection_position,
                "injection_depth": item.injection_depth,
                "injection_order": item.injection_order,
                "injection_trigger": list(item.injection_trigger),
                "marker_source": marker_source_label(item.identifier),
                # 占位条目的正文由运行时填充，界面须置灰（不可编辑）
                "content_editable": not (
                    item.is_marker or item.identifier in RUNTIME_FILLED_MARKERS
                ),
            }
            for item in preset.prompts
        ],
        "memory_injection": store.get_memory_injection(preset_id),
        "override": store.get_override(preset_id),
        "source_format": parsed.source_format,
        # 从索引读：剥离发生在导入那一刻，重新载入的文件里已无这些字段
        "stripped_keys": store.get_stripped_keys(preset_id),
        "warnings": parsed.warnings,
        "unsupported": parsed.unsupported,
    }


@router.get("")
def list_st_presets() -> dict:
    """已导入的 ST 预设清单（**不含任何提示词正文**）。

    合规：清单只暴露元信息；预设正文只在用户自己的浏览器与本地后端之间传递，
    存在 `ST_PRESETS_DIR`（已被 .gitignore 覆盖），不进仓库、不随发行物分发。
    """
    store = get_st_preset_store()
    return {
        "dir": str(store.root),
        "presets": [item.as_dict() for item in store.list_presets()],
    }


@router.post("/import")
def import_st_preset(req: StPresetImportRequest) -> dict:
    """导入一份 ST 预设（解析失败**不落盘**）。

    端点/密钥类字段在导入时被剥离且不保存（避免误存用户凭证）。
    """
    store = get_st_preset_store()
    try:
        summary = store.import_preset(
            req.content,
            preset_id=req.preset_id,
            source_file=req.source_file,
            overwrite=req.overwrite,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"预设解析失败：{exc}") from exc
    return {
        "preset": summary.as_dict(),
        "detail": _st_preset_detail(store, summary.id, store.load(summary.id)),
    }


@router.get("/{preset_id}")
def get_st_preset(preset_id: str) -> dict:
    """预设详情（归一化结构 + 条目清单 + 未生效特性）。"""
    store, parsed = _load_st_preset(preset_id)
    summary = next((item for item in store.list_presets() if item.id == preset_id), None)
    return {
        "preset": summary.as_dict() if summary is not None else {"id": preset_id},
        "detail": _st_preset_detail(store, preset_id, parsed),
    }


@router.patch("/{preset_id}")
def patch_st_preset(preset_id: str, req: StPresetPatchRequest) -> dict:
    """写入覆盖层（开关 / 顺序 / depth / role / 正文 / 记忆注入）。

    原始导入文件保持只读，因此“重置回导入时”永远可用。
    """
    store, _ = _load_st_preset(preset_id)
    patch = req.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="补丁为空：没有可写入的字段")
    _validate_st_patch(patch)
    store.patch_override(preset_id, patch)
    return {"detail": _st_preset_detail(store, preset_id, store.load(preset_id))}


@router.post("/{preset_id}/reset")
def reset_st_preset(preset_id: str) -> dict:
    """清空覆盖层，回到导入时的状态。"""
    store, _ = _load_st_preset(preset_id)
    store.reset_override(preset_id)
    return {"detail": _st_preset_detail(store, preset_id, store.load(preset_id))}


@router.get("/{preset_id}/export")
def export_st_preset(
    preset_id: str,
    apply_override: bool = Query(default=True, description="是否应用界面上的编辑"),
) -> dict:
    """导出为 ST 兼容 JSON，可直接带回酒馆使用。

    注意：导入时为安全剥离的端点/密钥类字段不会被还原（我们从未保存它们的值）。
    """
    store, _ = _load_st_preset(preset_id)
    return store.export_preset(preset_id, apply_override=apply_override)


@router.delete("/{preset_id}")
def delete_st_preset(preset_id: str) -> dict:
    """删除导入的预设及其覆盖层。"""
    store, _ = _load_st_preset(preset_id)
    store.delete_preset(preset_id)
    return {"deleted": preset_id}


