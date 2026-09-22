"""ST 预设解析与归一化（P1）。

职责边界：
- **本模块只做"读懂 + 归一化"**，不产出 messages（那是 P2 的 renderer）；
- **容错优先**：字段缺失、类型异常、identifier 重复、顺序表引用悬空等
  一律归一到可用状态并记入 `warnings`，而不是抛错中断导入；
- 真正无法处理的情况（顶层不是对象、JSON 解析失败）才抛 `ValueError`。

归一化规则（对齐 ST 的实际行为，依据见 docs/st-preset-compat.md）：
1. 剥离顶层端点/密钥类字段（`stripped_keys` 回传字段名，值一律丢弃）；
2. 条目：identifier 缺失按名称生成、重复加后缀，role 非法回退 system；
3. 运行时填充类 marker（chatHistory 等）正文强制清空；main/nsfw/jailbreak
   等自带正文的固定位**不清空**（这是 ST 的语义差异，容易搞错）；
4. 顺序表：缺失或为空 → 按条目定义顺序生成（全部启用）；多份 → 取第 1 份；
   引用悬空 → 移除；定义但未进顺序 → 视为未启用并记警告。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.prompts.st_compat.models import (
    MARKER_CHAR_DESCRIPTION,
    MARKER_CHAR_PERSONALITY,
    MARKER_CHAT_HISTORY,
    MARKER_DIALOGUE_EXAMPLES,
    MARKER_PERSONA_DESCRIPTION,
    MARKER_SCENARIO,
    MARKER_WORLD_INFO_AFTER,
    MARKER_WORLD_INFO_BEFORE,
    TRIGGER_UNSUPPORTED,
    VALID_ROLES,
    STPreset,
    STPromptItem,
    STPromptOrder,
    STPromptOrderEntry,
    strip_sensitive_keys,
)

# ---- 运行时填充正文的 marker（导入时强制清空正文）----
# 注意：main / nsfw / jailbreak / enhanceDefinitions **不在**此列——
# 它们的正文由预设自带，清空会导致预设静默失效。
RUNTIME_FILLED_MARKERS = frozenset(
    {
        MARKER_WORLD_INFO_BEFORE,
        MARKER_WORLD_INFO_AFTER,
        MARKER_CHAR_DESCRIPTION,
        MARKER_CHAR_PERSONALITY,
        MARKER_SCENARIO,
        MARKER_PERSONA_DESCRIPTION,
        MARKER_DIALOGUE_EXAMPLES,
        MARKER_CHAT_HISTORY,
    }
)

# ---- Text Completion 预设的特征字段（这些字段名不出现在 Chat Completion 预设里）----
_TEXT_COMPLETION_HINTS = (
    "temp",
    "rep_pen",
    "rep_pen_range",
    "sampler_priority",
    "sampler_order",
    "samplers",
    "max_length",
)

# ---- 群聊专有字段（本项目不支持，导入时提示）----
_GROUP_ONLY_FIELDS = ("group_nudge_prompt", "new_group_chat_prompt")

# ---- 续写/预填充相关字段（本项目暂无续写生成入口，导入时提示）----
_CONTINUATION_FIELDS = (
    ("continue_nudge_prompt", "续写提示"),
    ("assistant_prefill", "助手预填充"),
    ("continue_prefill", "续写预填充"),
    ("continue_postfix", "续写后缀"),
)

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass
class ParsedPreset:
    """一份已解析、已归一化的 ST 预设。"""

    preset: STPreset
    order: list[STPromptOrderEntry] = field(default_factory=list)
    prompts_by_id: dict[str, STPromptItem] = field(default_factory=dict)
    source_format: str = "chat"          # chat | text
    stripped_keys: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    order_index: int = -1                # 采用了第几份 prompt_order（-1 = 由定义顺序生成）
    raw: dict[str, Any] = field(default_factory=dict)   # 剥离敏感键后的原始内容（导出/落盘用）
    memory_injection: dict[str, Any] = field(default_factory=dict)  # HyPRA 记忆注入配置（见契约文档 §7）

    @property
    def ordered_items(self) -> list[tuple[STPromptItem, bool]]:
        """按生效顺序展开的 (条目, 是否启用)。"""
        return [
            (self.prompts_by_id[entry.identifier], entry.enabled)
            for entry in self.order
            if entry.identifier in self.prompts_by_id
        ]

    @property
    def enabled_items(self) -> list[STPromptItem]:
        """按生效顺序取出的已启用条目。"""
        return [item for item, enabled in self.ordered_items if enabled]

    def get(self, identifier: str) -> STPromptItem | None:
        """按 identifier 取条目。"""
        return self.prompts_by_id.get(identifier)


# --------------------------------------------------------------------------
# 归一化
# --------------------------------------------------------------------------


def detect_source_format(data: dict) -> str:
    """判断预设是 Chat Completion 还是 Text Completion 格式。

    判据：带非空 `prompts` 列表即 Chat Completion；否则看是否含 Text Completion
    的特征字段（`temp` / `rep_pen` / `max_length` 等）。
    """
    if isinstance(data.get("prompts"), list) and data["prompts"]:
        return "chat"
    if any(key in data for key in _TEXT_COMPLETION_HINTS):
        return "text"
    return "chat"


def slugify(text: str) -> str:
    """把名称转成可用标识（小写、连字符）：条目 identifier 与预设 id 共用。"""
    slug = _SLUG_PATTERN.sub("-", (text or "").strip().lower()).strip("-")
    return slug


def _normalize_prompts(
    raw_items: list[Any],
) -> tuple[list[STPromptItem], dict[str, STPromptItem], list[str]]:
    """归一化条目列表，返回 (条目列表, id 索引, 警告)。"""
    items: list[STPromptItem] = []
    by_id: dict[str, STPromptItem] = {}
    warnings: list[str] = []

    for index, raw in enumerate(raw_items):
        # 入参可能是原始 dict（直接调用本模块）或 STPreset 已校验过的实例（parse_st_preset 路径）
        if isinstance(raw, STPromptItem):
            item = raw.model_copy(deep=True)
        elif isinstance(raw, dict):
            item = STPromptItem.model_validate(raw)
        else:
            warnings.append(f"第 {index + 1} 个条目不是对象，已跳过")
            continue

        # ---- identifier：缺失则按名称生成，重复则加后缀 ----
        identifier = (item.identifier or "").strip()
        if not identifier:
            identifier = slugify(item.name) or f"prompt-{index + 1}"
            warnings.append(f"第 {index + 1} 个条目缺少 identifier，已按名称生成「{identifier}」")
        base, suffix = identifier, 2
        while identifier in by_id:
            identifier = f"{base}-{suffix}"
            suffix += 1
        if identifier != base:
            warnings.append(f"条目 identifier「{base}」重复，已重命名为「{identifier}」")

        # ---- role：非法值回退 system ----
        if item.role not in VALID_ROLES:
            warnings.append(
                f"条目「{identifier}」的角色「{item.role}」不受支持，已回退 system"
            )
            item.role = "system"

        # ---- 正文：运行时填充类 marker 强制清空 ----
        if item.marker or identifier in RUNTIME_FILLED_MARKERS:
            if item.content:
                warnings.append(f"条目「{identifier}」为占位条目，其正文已忽略（内容由运行时填充）")
            item.content = ""

        item.identifier = identifier
        if not item.name:
            item.name = identifier
        items.append(item)
        by_id[identifier] = item

    return items, by_id, warnings


def _normalize_order(
    raw_orders: list[STPromptOrder],
    prompts_by_id: dict[str, STPromptItem],
) -> tuple[list[STPromptOrderEntry], int, list[str]]:
    """归一化顺序表，返回 (生效顺序, 采用第几份, 警告)。"""
    warnings: list[str] = []

    if len(raw_orders) > 1:
        warnings.append(
            f"预设包含 {len(raw_orders)} 份顺序表（按角色卡区分），本项目采用第 1 份"
        )

    if not raw_orders or not raw_orders[0].order:
        warnings.append("预设未提供条目顺序，已按条目定义顺序生成（全部启用）")
        return (
            [STPromptOrderEntry(identifier=item.identifier, enabled=True) for item in prompts_by_id.values()],
            -1,
            warnings,
        )

    order: list[STPromptOrderEntry] = []
    seen: set[str] = set()
    for entry in raw_orders[0].order:
        identifier = (entry.identifier or "").strip()
        if not identifier:
            continue
        if identifier not in prompts_by_id:
            warnings.append(f"顺序表引用了不存在的条目「{identifier}」，已移除")
            continue
        if identifier in seen:
            warnings.append(f"顺序表重复引用条目「{identifier}」，已去重")
            continue
        seen.add(identifier)
        order.append(STPromptOrderEntry(identifier=identifier, enabled=bool(entry.enabled)))

    unused = [item.display_name for ident, item in prompts_by_id.items() if ident not in seen]
    if unused:
        warnings.append(
            "以下条目已定义但未加入顺序表，视为未启用：" + "、".join(unused)
        )

    return order, 0, warnings


def _field_is_set(preset: STPreset, field: str) -> bool:
    """字段是否被预设显式设置（字符串看非空，布尔看 True）。"""
    value = getattr(preset, field, None)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _collect_unsupported(
    preset: STPreset,
    by_id: dict[str, STPromptItem],
    source_format: str,
) -> list[str]:
    """收集"本项目未生效"的特性（界面提示用，不报错）。"""
    unsupported: list[str] = []

    if source_format == "text":
        unsupported.append(
            "该预设为 Text Completion 格式，提示词组装不适用，仅采样参数可用"
        )

    for key in _GROUP_ONLY_FIELDS:
        if str(getattr(preset, key, "") or "").strip():
            unsupported.append(f"群聊专有设置「{key}」在本项目不生效（单人陪伴场景）")

    ignored = [label for field, label in _CONTINUATION_FIELDS if _field_is_set(preset, field)]
    if ignored:
        unsupported.append(
            "续写/预填充相关设置（" + "、".join(ignored) + "）在本项目不生效（暂无续写生成入口）"
        )

    for identifier, item in by_id.items():
        hits = [t for t in item.injection_trigger if t in TRIGGER_UNSUPPORTED]
        if hits:
            unsupported.append(
                f"条目「{item.display_name}」限定的生成类型 {'/'.join(hits)} 本项目不支持，该条目将按常规生成处理"
            )

    return unsupported


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def parse_st_preset(data: dict) -> ParsedPreset:
    """解析并归一化一份 ST 预设（输入为已反序列化的 JSON 对象）。"""
    if not isinstance(data, dict):
        raise ValueError("预设顶层必须是 JSON 对象（dict）")

    cleaned, stripped = strip_sensitive_keys(data)
    source_format = detect_source_format(cleaned)

    preset = STPreset.model_validate(cleaned)
    prompts, by_id, prompt_warnings = _normalize_prompts(preset.prompts)
    order, order_index, order_warnings = _normalize_order(preset.prompt_order, by_id)
    preset.prompts = prompts

    warnings = [*prompt_warnings, *order_warnings]
    if stripped:
        warnings.append(
            "出于安全考虑，已忽略且未保存端点/密钥类字段：" + "、".join(stripped)
        )

    return ParsedPreset(
        preset=preset,
        order=order,
        prompts_by_id=by_id,
        source_format=source_format,
        stripped_keys=stripped,
        warnings=warnings,
        unsupported=_collect_unsupported(preset, by_id, source_format),
        order_index=order_index,
        raw=deepcopy(cleaned),
    )


def parse_st_preset_text(text: str) -> ParsedPreset:
    """从 JSON 文本解析预设（容忍 UTF-8 BOM）。"""
    raw = text.lstrip("\ufeff")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"预设不是合法的 JSON：{exc}") from exc
    return parse_st_preset(data)


def load_st_preset_file(path: str | Path) -> ParsedPreset:
    """从文件加载并解析预设（UTF-8-BOM 容忍）。"""
    file_path = Path(path)
    return parse_st_preset_text(file_path.read_text(encoding="utf-8-sig"))
