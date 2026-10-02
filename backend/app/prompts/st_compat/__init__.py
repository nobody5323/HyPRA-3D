"""SillyTavern 预设兼容层。

分阶段落地（见 docs/st-preset-compat.md §14）：

- **P1（当前）解析与存储**：`models`（数据模型）/ `parser`（归一化）/
  `store`（本地存储 + 覆盖层）；
- P2 组装渲染（`renderer` / `markers`）：ST 的 `prompt_order` → messages；
- P3 宏适配；P4 接口接入；P5/P6 前端面板。

合规：本包只解析与渲染**用户本地导入**的预设，不在仓库或发行物中内置任何
SillyTavern / 社区预设的提示词原文（见 AGENTS.md §6 与契约文档 §1）。
"""

from app.prompts.st_compat.models import (
    DEFAULT_DEPTH,
    DEFAULT_ORDER,
    MARKER_IDENTIFIERS,
    MARKER_WORLD_INFO_AFTER,
    MARKER_WORLD_INFO_BEFORE,
    POSITION_ABSOLUTE,
    POSITION_RELATIVE,
    TRIGGER_SUPPORTED,
    TRIGGER_UNSUPPORTED,
    VALID_ROLES,
    STPreset,
    STPromptItem,
    STPromptOrder,
    STPromptOrderEntry,
    strip_sensitive_keys,
)
from app.prompts.st_compat.parser import (
    RUNTIME_FILLED_MARKERS,
    ParsedPreset,
    detect_source_format,
    load_st_preset_file,
    parse_st_preset,
    parse_st_preset_text,
    slugify,
)
from app.prompts.st_compat.macros import MacroContext, render_macros
from app.prompts.st_compat.markers import (
    MARKER_SOURCES,
    STREAM_MARKERS,
    TEXT_MARKERS,
    marker_source_label,
)
from app.prompts.st_compat.renderer import (
    RenderedPrompt,
    STRenderContext,
    render_st_preset,
)
from app.prompts.st_compat.store import (
    DEFAULT_MEMORY_INJECTION,
    default_root,
    MEMORY_POSITIONS,
    PresetSummary,
    StPresetStore,
    apply_override,
    normalize_memory_injection,
)

__all__ = [
    # models
    "DEFAULT_DEPTH",
    "DEFAULT_ORDER",
    "MARKER_IDENTIFIERS",
    "MARKER_WORLD_INFO_AFTER",
    "MARKER_WORLD_INFO_BEFORE",
    "POSITION_ABSOLUTE",
    "POSITION_RELATIVE",
    "TRIGGER_SUPPORTED",
    "TRIGGER_UNSUPPORTED",
    "VALID_ROLES",
    "STPreset",
    "STPromptItem",
    "STPromptOrder",
    "STPromptOrderEntry",
    "strip_sensitive_keys",
    # parser
    "RUNTIME_FILLED_MARKERS",
    "ParsedPreset",
    "detect_source_format",
    "load_st_preset_file",
    "parse_st_preset",
    "parse_st_preset_text",
    "slugify",
    # store
    "DEFAULT_MEMORY_INJECTION",
    "default_root",
    "MEMORY_POSITIONS",
    "PresetSummary",
    "StPresetStore",
    "apply_override",
    "normalize_memory_injection",
    # markers
    "MARKER_SOURCES",
    "STREAM_MARKERS",
    "TEXT_MARKERS",
    "marker_source_label",
    # macros
    "MacroContext",
    "render_macros",
    # renderer
    "RenderedPrompt",
    "STRenderContext",
    "render_st_preset",
]
