"""ST marker 填充：把预设置位符映射到 HyPRA 的运行时内容（契约文档 §5）。

三类条目的边界要分清（这是最容易搞错的地方）：

1. **运行时填充类 marker**（本模块负责）：预设正文为空，内容来自 HyPRA
   （人设 / 世界书 / 历史 / 示例 / 用户描述）。其中文本类产出**一条消息**，
   消息流类（`dialogueExamples` / `chatHistory`）产出**多条消息**，由 renderer 特殊处理；
2. **自带正文类条目**（`main` / `nsfw` / `jailbreak` / `enhanceDefinitions`）：
   正文来自预设自身，仅当 `use_sysprompt` 开启时才被覆盖文本替换；
3. **普通条目**：正文来自预设。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.prompts.st_compat.models import (
    MARKER_CHAR_DESCRIPTION,
    MARKER_CHAR_PERSONALITY,
    MARKER_CHAT_HISTORY,
    MARKER_DIALOGUE_EXAMPLES,
    MARKER_PERSONA_DESCRIPTION,
    MARKER_SCENARIO,
    MARKER_WORLD_INFO_AFTER,
    MARKER_WORLD_INFO_BEFORE,
)
from app.prompts.st_compat.parser import RUNTIME_FILLED_MARKERS

if TYPE_CHECKING:   # 仅在类型检查时导入，避免运行期循环依赖
    from app.prompts.st_compat.models import STPreset
    from app.prompts.st_compat.renderer import STRenderContext

# 运行时填充的**消息流**类 marker（产出多条消息）
STREAM_MARKERS = frozenset({MARKER_DIALOGUE_EXAMPLES, MARKER_CHAT_HISTORY})

# 运行时填充的**文本**类 marker（产出一条消息）
TEXT_MARKERS = frozenset(RUNTIME_FILLED_MARKERS - STREAM_MARKERS)

# marker → 内容来源（供界面展示"这个槽位在本项目由什么填充"）
MARKER_SOURCES: dict[str, str] = {
    MARKER_WORLD_INFO_BEFORE: "世界书命中（人设之前）",
    MARKER_WORLD_INFO_AFTER: "世界书命中（人设之后）",
    MARKER_CHAR_DESCRIPTION: "角色人设正文",
    MARKER_CHAR_PERSONALITY: "角色性格（人设预设的定位与标签）",
    MARKER_SCENARIO: "场景（本项目暂无对应字段，默认空）",
    MARKER_PERSONA_DESCRIPTION: "对话者描述（默认空）",
    MARKER_DIALOGUE_EXAMPLES: "文风预设的示例对话",
    MARKER_CHAT_HISTORY: "滚动窗口历史 + 本次输入（含 In-Chat 注入）",
}


def apply_format(
    template: str,
    value: str,
    placeholder: str,
    *,
    marker: str,
) -> tuple[str, list[str]]:
    """按 ST 的"格式模板"包装内容，返回 (结果文本, 警告)。

    规则：
    - 模板为空 → 原样注入（ST 文档：未设置模板则不加包装）；
    - 模板含 placeholder（或 `{0}`）→ 替换占位符；
    - 模板不含任何占位符 → **忽略模板并原样注入**，同时记一条警告。
      说明：ST 在同样情况下会静默丢掉整段内容（`format.replace` 找不到占位符即返回模板本身），
      这里选择容错——宁可包装丢失，也不让内容凭空消失。
    """
    text = value or ""
    if not template.strip() or not text.strip():
        return text, []

    replaced: str | None = None
    if placeholder and placeholder in template:
        replaced = template.replace(placeholder, text)
    elif "{0}" in template:
        replaced = template.replace("{0}", text)

    if replaced is None:
        return text, [
            f"格式模板（{marker}）缺少占位符 {placeholder or '{0}'}，已忽略模板并原样注入内容"
        ]
    return replaced, []


def fill_text_marker(
    identifier: str,
    preset_content: str,
    context: "STRenderContext",
    preset: "STPreset",
) -> tuple[str, list[str]]:
    """填充一个**文本类** marker，返回 (文本, 警告)。

    非运行时 marker（main / nsfw / jailbreak / enhanceDefinitions / 未知标识）
    直接返回预设自带正文——这些条目的内容由预设提供，HyPRA 不替换。
    """
    warnings: list[str] = []

    if identifier == MARKER_CHAR_DESCRIPTION:
        return context.persona_text, warnings

    if identifier == MARKER_CHAR_PERSONALITY:
        return apply_format(
            preset.personality_format,
            context.persona_personality,
            "{{personality}}",
            marker=identifier,
        )

    if identifier == MARKER_SCENARIO:
        return apply_format(
            preset.scenario_format,
            context.scenario_text,
            "{{scenario}}",
            marker=identifier,
        )

    if identifier == MARKER_PERSONA_DESCRIPTION:
        return context.user_persona, warnings

    if identifier == MARKER_WORLD_INFO_BEFORE:
        return apply_format(preset.wi_format, context.worldbook_before, "{0}", marker=identifier)

    if identifier == MARKER_WORLD_INFO_AFTER:
        return apply_format(preset.wi_format, context.worldbook_after, "{0}", marker=identifier)

    return preset_content, warnings


def marker_source_label(identifier: str) -> str:
    """marker 的内容来源说明（界面展示用；未知 marker 返回空串）。"""
    return MARKER_SOURCES.get(identifier, "")
