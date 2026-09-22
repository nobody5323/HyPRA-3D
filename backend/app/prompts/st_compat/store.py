"""ST 预设的本地存储与覆盖层（P1）。

目录约定（见 docs/st-preset-compat.md §9）：

    <root>/                        # 默认 backend/data/presets（.gitignore 已覆盖 backend/data/）
        <preset-id>.json           # 用户导入的原始文件（只读，永不改写）
        <preset-id>.override.json  # 界面编辑结果（开关/排序/depth/正文/记忆注入）
        index.json                 # 清单：名称、来源文件名、导入时间、原始文件 sha256

三条关键约定：

1. **原始文件只读**：界面上的任何编辑写进 `<id>.override.json`，
   因此"重置为导入时"永远可用，也不会把用户的酒馆预设改坏；
2. **原始文件被替换即作废旧编辑**：载入时比对 sha256，不一致说明用户换了
   同名文件，旧 override 对新内容无意义，直接作废并记录警告；
3. **合规**：这里存的是**用户本地数据**（用户自己导入的预设），
   `backend/data/` 已被 .gitignore 覆盖，不进仓库、不随发行物分发。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.prompts.st_compat.models import (
    VALID_ROLES,
    STPreset,
    STPromptOrderEntry,
    strip_sensitive_keys,
)
from app.prompts.st_compat.parser import (
    RUNTIME_FILLED_MARKERS,
    ParsedPreset,
    parse_st_preset,
    parse_st_preset_text,
    slugify,
)

# 预设目录默认位置：本文件位于 backend/app/prompts/st_compat/store.py
DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "presets"

_INDEX_FILE = "index.json"
_INDEX_VERSION = 1

# preset_id 允许的字符集：小写字母数字开头，其后允许 . _ -（用于版本号式命名）
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# 覆盖层里允许修改的条目字段
_OVERRIDABLE_ITEM_FIELDS = (
    "name",
    "content",
    "role",
    "system_prompt",
    "forbid_overrides",
    "injection_position",
    "injection_depth",
    "injection_order",
    "injection_trigger",
)

# 采样参数导出时的字段名映射（我方 key → ST 字段名）
_SAMPLING_EXPORT_MAP = {"max_tokens": "openai_max_tokens"}

# HyPRA 记忆注入的默认配置（契约文档 §7：IN_CHAT / depth=1 / role=system / order=100）
DEFAULT_MEMORY_INJECTION: dict[str, Any] = {
    "enabled": True,
    "position": "in_chat",   # in_chat | in_prompt | world_info_before | world_info_after | off
    "depth": 1,
    "role": "system",
    "order": 100,
}

MEMORY_POSITIONS = ("in_chat", "in_prompt", "world_info_before", "world_info_after", "off")


@dataclass
class PresetSummary:
    """预设清单项（列表接口用，不返回正文）。"""

    id: str
    name: str
    source_file: str
    imported_at: str
    prompt_count: int
    enabled_count: int
    sampling: dict[str, float | int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    has_override: bool = False
    sha256: str = ""

    def as_dict(self) -> dict:
        """转成可 JSON 序列化的字典（不含任何提示词正文）。"""
        return {
            "id": self.id,
            "name": self.name,
            "source_file": self.source_file,
            "imported_at": self.imported_at,
            "prompt_count": self.prompt_count,
            "enabled_count": self.enabled_count,
            "sampling": dict(self.sampling),
            "warnings": list(self.warnings),
            "unsupported": list(self.unsupported),
            "has_override": self.has_override,
            "sha256": self.sha256,
        }


class StPresetStore:
    """导入预设的本地存储门面。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root is not None else DEFAULT_ROOT

    # ---------- 路径与校验 ----------

    @property
    def root(self) -> Path:
        return self._root

    def _validate_id(self, preset_id: str) -> str:
        """校验 preset_id，防目录穿越（拒绝 `..` 与非法字符）。"""
        if not isinstance(preset_id, str) or not _ID_PATTERN.match(preset_id) or ".." in preset_id:
            raise ValueError(f"非法的预设 id：{preset_id!r}（只允许小写字母/数字/.-_）")
        return preset_id

    def _raw_path(self, preset_id: str) -> Path:
        return self._root / f"{self._validate_id(preset_id)}.json"

    def _override_path(self, preset_id: str) -> Path:
        return self._root / f"{self._validate_id(preset_id)}.override.json"

    def _index_path(self) -> Path:
        return self._root / _INDEX_FILE

    def has_preset(self, preset_id: str) -> bool:
        """该 id 是否已导入。"""
        try:
            return self._raw_path(preset_id).is_file()
        except ValueError:
            return False

    # ---------- 读写工具 ----------

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        """原子写：先写临时文件再替换，避免中途失败留下半个文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _sha256(raw_text: str) -> str:
        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    def _read_index(self) -> dict:
        path = self._index_path()
        if not path.is_file():
            return {"version": _INDEX_VERSION, "presets": {}}
        try:
            data = self._read_json(path)
        except (OSError, json.JSONDecodeError):
            # 索引损坏不应导致整个功能不可用：按空索引重建（原始文件仍在）
            return {"version": _INDEX_VERSION, "presets": {}}
        if not isinstance(data, dict) or not isinstance(data.get("presets"), dict):
            return {"version": _INDEX_VERSION, "presets": {}}
        return data

    def _write_index(self, index: dict) -> None:
        self._write_json(self._index_path(), index)

    def _raw_text(self, preset_id: str) -> str:
        return self._raw_path(preset_id).read_text(encoding="utf-8-sig")

    def get_name(self, preset_id: str) -> str:
        """预设展示名（取自索引，缺失时回落到 id）。"""
        entry = self._read_index().get("presets", {}).get(preset_id, {})
        return str(entry.get("name") or preset_id)

    # ---------- 导入 / 清单 ----------

    def _allocate_id(self, base: str) -> str:
        """在 `<base>` 基础上找一个空闲 id（冲突时追加 -2、-3…）。"""
        candidate = base
        suffix = 2
        while self.has_preset(candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def import_preset(
        self,
        data: dict | str | bytes,
        *,
        preset_id: str | None = None,
        source_file: str = "",
        overwrite: bool = False,
    ) -> PresetSummary:
        """导入一份 ST 预设。

        参数:
            data: 已反序列化的 dict / JSON 文本 / 文件字节；
            preset_id: 显式指定 id；缺省由来源文件名推导，冲突时自动加后缀；
            source_file: 原始文件名（仅作展示与推导 id 用）；
            overwrite: True 时覆盖同名预设并作废其旧编辑。

        Raises:
            ValueError: JSON 非法或顶层不是对象（此时**不落盘**）。
        """
        raw_dict = _coerce_to_dict(data)
        parsed = parse_st_preset(raw_dict)   # 先解析：失败即不落盘

        if preset_id is not None:
            self._validate_id(preset_id)
            if self.has_preset(preset_id) and not overwrite:
                raise ValueError(f"预设 id「{preset_id}」已存在（如需覆盖请显式指定）")
            target_id = preset_id
        else:
            base = slugify(Path(source_file).stem) or "st-preset"
            target_id = self._allocate_id(base)

        # 落盘内容 = 原始内容（已剥离端点/密钥类字段），不做归一化改写
        path = self._raw_path(target_id)
        self._write_json(path, parsed.raw)
        text = json.dumps(parsed.raw, ensure_ascii=False, indent=2)

        if overwrite and self._override_path(target_id).is_file():
            self._override_path(target_id).unlink()

        index = self._read_index()
        name = Path(source_file).stem or target_id
        index["presets"][target_id] = {
            "name": name,
            "source_file": source_file,
            "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sha256": self._sha256(text),
        }
        self._write_index(index)

        return self._summarize(target_id, parsed, index)

    def list_presets(self) -> list[PresetSummary]:
        """列出全部已导入预设（按名称排序）。"""
        index = self._read_index()
        summaries: list[PresetSummary] = []
        for preset_id in sorted(index.get("presets", {})):
            if not self.has_preset(preset_id):
                continue   # 索引里有但文件被删：跳过（不自动清理，留给 delete 显式处理）
            try:
                summaries.append(self._summarize(preset_id, self.load(preset_id), index))
            except (OSError, ValueError):
                continue   # 单个文件损坏不影响整份清单
        summaries.sort(key=lambda item: item.name)
        return summaries

    def _summarize(
        self, preset_id: str, parsed: ParsedPreset, index: dict | None = None
    ) -> PresetSummary:
        index = index if index is not None else self._read_index()
        entry = index.get("presets", {}).get(preset_id, {})
        return PresetSummary(
            id=preset_id,
            name=str(entry.get("name") or preset_id),
            source_file=str(entry.get("source_file") or ""),
            imported_at=str(entry.get("imported_at") or ""),
            prompt_count=len(parsed.preset.prompts),
            enabled_count=sum(1 for _, enabled in parsed.ordered_items if enabled),
            sampling=dict(parsed.preset.sampling),
            warnings=list(parsed.warnings),
            unsupported=list(parsed.unsupported),
            has_override=self._override_path(preset_id).is_file(),
            sha256=str(entry.get("sha256") or ""),
        )

    # ---------- 载入（含覆盖层） ----------

    def load(self, preset_id: str) -> ParsedPreset:
        """载入预设：原始文件 ⊕ 覆盖层，并返回归一化结果。

        副作用：若原始文件的 sha256 与索引不一致（用户替换了文件），
        会作废旧 override 并更新索引——这是"编辑永远对应当前文件"的保证。
        """
        if not self.has_preset(preset_id):
            raise FileNotFoundError(f"预设不存在：{preset_id}")

        text = self._raw_text(preset_id)
        parsed = parse_st_preset_text(text)
        digest = self._sha256(text)

        index = self._read_index()
        entry = index.get("presets", {}).get(preset_id)
        if entry is None:
            entry = {"name": preset_id, "source_file": "", "imported_at": "", "sha256": digest}
            index.setdefault("presets", {})[preset_id] = entry
            self._write_index(index)
        elif entry.get("sha256") and entry["sha256"] != digest:
            if self._override_path(preset_id).is_file():
                self._override_path(preset_id).unlink()
                parsed.warnings.append("原始预设文件已变更，此前的界面编辑已作废")
            entry["sha256"] = digest
            self._write_index(index)

        override = self.get_override(preset_id)
        apply_override(parsed, override)
        return parsed

    # ---------- 覆盖层 ----------

    def get_override(self, preset_id: str) -> dict:
        """读取覆盖层（不存在则返回空字典）。"""
        path = self._override_path(preset_id)
        if not path.is_file():
            return {}
        try:
            data = self._read_json(path)
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get_memory_injection(self, preset_id: str) -> dict:
        """读取生效的记忆注入配置（缺省值兜底）。"""
        override = self.get_override(preset_id)
        return normalize_memory_injection(override.get("memory_injection"))

    def patch_override(self, preset_id: str, patch: dict) -> dict:
        """合并写入覆盖层（深合并），返回合并后的完整覆盖层。"""
        if not self.has_preset(preset_id):
            raise FileNotFoundError(f"预设不存在：{preset_id}")
        if not isinstance(patch, dict):
            raise ValueError("patch 必须是对象")
        merged = _deep_merge(self.get_override(preset_id), patch)
        self._write_json(self._override_path(preset_id), merged)
        return merged

    def reset_override(self, preset_id: str) -> None:
        """清空覆盖层，回到导入时的状态。"""
        path = self._override_path(preset_id)
        if path.is_file():
            path.unlink()

    def delete_preset(self, preset_id: str) -> None:
        """删除预设及其覆盖层与索引条目。"""
        raw_path = self._raw_path(preset_id)
        if not raw_path.is_file():
            raise FileNotFoundError(f"预设不存在：{preset_id}")
        raw_path.unlink()
        self.reset_override(preset_id)
        index = self._read_index()
        index.get("presets", {}).pop(preset_id, None)
        self._write_index(index)

    # ---------- 导出 ----------

    def export_preset(self, preset_id: str, *, apply_override: bool = True) -> dict:
        """导出为 ST 兼容 JSON。

        注意：导入时为安全剥离的端点/密钥类字段**不会**被还原（我们从未保存它们的值），
        导出结果可直接在酒馆里导入使用，但 provider/端点设置需在酒馆中重新选择。
        """
        parsed = self.load(preset_id) if apply_override else self._load_without_override(preset_id)
        if not apply_override:
            return deepcopy(parsed.raw)

        data = deepcopy(parsed.raw)
        sampling = parsed.preset.sampling
        for key, value in sampling.items():
            data[_SAMPLING_EXPORT_MAP.get(key, key)] = value

        prompts_by_id = {item.get("identifier"): item for item in data.get("prompts", []) if isinstance(item, dict)}
        for item in parsed.preset.prompts:
            target = prompts_by_id.get(item.identifier)
            if target is None:
                continue
            for field_name in _OVERRIDABLE_ITEM_FIELDS:
                target[field_name] = getattr(item, field_name)

        orders = data.get("prompt_order")
        if isinstance(orders, list) and orders and isinstance(orders[0], dict):
            orders[0]["order"] = [
                {"identifier": entry.identifier, "enabled": entry.enabled} for entry in parsed.order
            ]
        return data

    def _load_without_override(self, preset_id: str) -> ParsedPreset:
        """载入原始预设（不应用覆盖层，也不做 hash 校验写回）。"""
        if not self.has_preset(preset_id):
            raise FileNotFoundError(f"预设不存在：{preset_id}")
        return parse_st_preset_text(self._raw_text(preset_id))


# --------------------------------------------------------------------------
# 覆盖层应用
# --------------------------------------------------------------------------


def _coerce_to_dict(data: dict | str | bytes) -> dict:
    """把 dict / JSON 文本 / 字节统一成 dict。"""
    if isinstance(data, dict):
        return deepcopy(data)
    if isinstance(data, bytes):
        text = data.decode("utf-8-sig")
    elif isinstance(data, str):
        text = data.lstrip("\ufeff")
    else:
        raise TypeError(f"不支持的输入类型：{type(data).__name__}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"预设不是合法的 JSON：{exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("预设顶层必须是 JSON 对象（dict）")
    return parsed


def _deep_merge(base: dict, patch: dict) -> dict:
    """深合并 patch 到 base（patch 优先，不改动入参）。

    `null` 表示**删除该覆盖项**（界面上的「恢复预设原值」）：
    否则一旦调过某项，就再也回不到导入时的取值，只能整体重置。
    """
    result = deepcopy(base)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
            continue
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def normalize_memory_injection(raw: Any) -> dict:
    """归一化记忆注入配置（缺省值兜底 + 非法值回退）。

    供 store（读覆盖层）与 renderer（渲染时确定注入位置）共用。
    """
    config = dict(DEFAULT_MEMORY_INJECTION)
    if not isinstance(raw, dict):
        return config

    if isinstance(raw.get("enabled"), bool):
        config["enabled"] = raw["enabled"]
    if raw.get("position") in MEMORY_POSITIONS:
        config["position"] = raw["position"]
    if raw.get("role") in VALID_ROLES:
        config["role"] = raw["role"]
    for key in ("depth", "order"):
        value = raw.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            config[key] = value
    return config


def apply_override(parsed: ParsedPreset, override: dict) -> ParsedPreset:
    """把覆盖层应用到已解析的预设上（原地修改并返回同一对象）。

    只接受已知字段：未知键忽略（避免脏数据污染运行结果）。
    marker 条目的正文不可覆盖（内容由运行时填充）。
    """
    if not override:
        parsed.memory_injection = normalize_memory_injection(None)
        return parsed

    # ---- 采样参数 ----
    sampling_patch = override.get("sampling")
    if isinstance(sampling_patch, dict):
        preset_fields = set(STPreset.model_fields)
        for key, value in sampling_patch.items():
            if key in preset_fields and value is not None:
                setattr(parsed.preset, key, value)

    # ---- 组装开关 ----
    assembly_patch = override.get("assembly")
    if isinstance(assembly_patch, dict):
        if isinstance(assembly_patch.get("use_sysprompt"), bool):
            parsed.preset.use_sysprompt = assembly_patch["use_sysprompt"]
        if isinstance(assembly_patch.get("squash_system_messages"), bool):
            parsed.preset.squash_system_messages = assembly_patch["squash_system_messages"]
        names = assembly_patch.get("names_behavior")
        if isinstance(names, int) and not isinstance(names, bool) and names in (0, 1, 2):
            parsed.preset.names_behavior = names

    # ---- 条目字段 ----
    items_patch = override.get("prompts")
    if isinstance(items_patch, dict):
        for identifier, fields in items_patch.items():
            item = parsed.prompts_by_id.get(identifier)
            if item is None or not isinstance(fields, dict):
                continue
            for field_name, value in fields.items():
                if field_name not in _OVERRIDABLE_ITEM_FIELDS or value is None:
                    continue
                if field_name == "content" and (item.marker or identifier in RUNTIME_FILLED_MARKERS):
                    parsed.warnings.append(f"条目「{identifier}」为占位条目，正文覆盖已忽略")
                    continue
                if field_name == "role" and value not in VALID_ROLES:
                    parsed.warnings.append(
                        f"条目「{identifier}」的角色覆盖值「{value}」非法，已忽略"
                    )
                    continue
                setattr(item, field_name, value)

    # ---- 顺序与启用状态 ----
    order_patch = override.get("prompt_order")
    entries_patch: dict[str, bool] = {}
    if isinstance(items_patch, dict):
        for identifier, fields in items_patch.items():
            if isinstance(fields, dict) and "enabled" in fields:
                entries_patch[identifier] = bool(fields["enabled"])
    if isinstance(order_patch, list) and order_patch:
        reordered: list[STPromptOrderEntry] = []
        seen: set[str] = set()
        for identifier in order_patch:
            if not isinstance(identifier, str) or identifier in seen:
                continue
            if identifier not in parsed.prompts_by_id:
                parsed.warnings.append(f"覆盖层顺序引用了不存在的条目「{identifier}」，已忽略")
                continue
            seen.add(identifier)
            enabled = entries_patch.get(identifier)
            if enabled is None:
                current = next((e.enabled for e in parsed.order if e.identifier == identifier), True)
                enabled = current
            reordered.append(STPromptOrderEntry(identifier=identifier, enabled=enabled))
        # 覆盖层未提及的条目追加到末尾（保持原有启用状态），避免编辑后条目凭空消失
        for entry in parsed.order:
            if entry.identifier not in seen and entry.identifier in parsed.prompts_by_id:
                reordered.append(entry)
        parsed.order = reordered
    elif entries_patch:
        for entry in parsed.order:
            if entry.identifier in entries_patch:
                entry.enabled = entries_patch[entry.identifier]

    parsed.memory_injection = normalize_memory_injection(override.get("memory_injection"))
    return parsed


def read_raw_preset(path: str | Path) -> tuple[dict, list[str]]:
    """读取一份 ST 预设文件，返回 (已剥离敏感字段的内容, 被剥离的字段名)。

    供"先看再导入"的场景使用（不落盘）。
    """
    file_path = Path(path)
    data = json.loads(file_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("预设顶层必须是 JSON 对象（dict）")
    return strip_sensitive_keys(data)
