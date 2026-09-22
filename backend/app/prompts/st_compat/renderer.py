"""ST 预设组装渲染：`prompt_order` → messages（契约文档 §6）。

复现的组装语义（对齐 ST `public/scripts/openai.js`）：

    ① 处理记忆扩展注入的位置（in_chat / in_prompt / world_info_* / off）
    ② 计算各条目生效正文（含 use_sysprompt 覆盖）
    ③ 构建历史块 = 历史消息 + 本次输入
    ④ In-Chat 注入插进历史块（同 depth · 同 order · 同 role 合并成一条消息）
    ⑤ 按 prompt_order 顺序展开相对条目；marker 就地填充
       （chatHistory → ③④ 的结果；dialogueExamples → few-shot 示例）
    ⑥ 可选：合并相邻 system 消息（squash_system_messages）

设计边界：
- 本模块**不改动** `PromptManager`（内置分层模式继续走原路径），
  两条路径在 `graph.nodes.assemble_prompt` 分派；
- 宏替换（`{{char}}` 等）属 P3，本步不做——输入文本原样进入 messages；
- 输入契约由调用方（P4 的 graph 节点）填充，见 `STRenderContext`。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace

from app.prompts.st_compat.macros import MacroContext, render_macros
from app.prompts.st_compat.markers import TEXT_MARKERS, fill_text_marker
from app.prompts.st_compat.models import (
    MARKER_CHAT_HISTORY,
    MARKER_DIALOGUE_EXAMPLES,
    MARKER_JAILBREAK,
)
from app.prompts.st_compat.parser import ParsedPreset
from app.prompts.st_compat.store import normalize_memory_injection
from app.session.context import ChatTurn

# In-Chat 注入的角色顺序（对齐 ST 源码：system → user → assistant）
_ROLE_ORDER = ("system", "user", "assistant")


@dataclass
class STRenderContext:
    """渲染一份 ST 预设所需的运行时内容（P4 由 graph 节点填充）。"""

    # ---- marker 内容来源 ----
    persona_text: str = ""            # → charDescription
    persona_personality: str = ""     # → charPersonality
    scenario_text: str = ""           # → scenario
    user_persona: str = ""            # → personaDescription
    worldbook_before: str = ""        # → worldInfoBefore
    worldbook_after: str = ""         # → worldInfoAfter
    history: list[ChatTurn] = field(default_factory=list)   # → chatHistory
    examples: list[tuple[str, str]] = field(default_factory=list)  # → dialogueExamples
    user_input: str = ""

    # ---- HyPRA 自有内容 ----
    memory_text: str = ""             # 记忆块（个人记忆 / 情景记忆 / 语义事实，已拼好）
    style_text: str = ""              # 文风块（追加到 jailbreak 位之后，见契约文档 §6.6）
    system_prompt_override: str = ""  # use_sysprompt 的覆盖文本（用户提供）

    # ---- 名称（供 P3 宏替换使用；本阶段仅用于 names_behavior 前缀）----
    persona_name: str = ""
    user_name: str = ""

    # ---- 生成类型（injection_trigger 过滤用）----
    generation_type: str = "normal"   # normal | regenerate

    # ---- 会话级宏变量（{{setvar::}} 的就地存储；按会话持有同一份字典）----
    macro_variables: dict[str, str] = field(default_factory=dict)


@dataclass
class RenderedPrompt:
    """渲染结果。"""

    messages: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    used_markers: list[str] = field(default_factory=list)
    empty_markers: list[str] = field(default_factory=list)
    in_chat_count: int = 0            # In-Chat 注入产生的消息数
    example_count: int = 0            # few-shot 示例组数
    unresolved_macros: list[str] = field(default_factory=list)   # 未实现、已保留原文的宏

    @property
    def system_prompt(self) -> str:
        """全部 system 消息的拼接（调试与界面预览用）。"""
        return "\n\n".join(
            message["content"] for message in self.messages if message.get("role") == "system"
        )


def render_st_preset(
    parsed: ParsedPreset,
    context: STRenderContext,
    *,
    memory_injection: dict | None = None,
) -> RenderedPrompt:
    """按 ST 语义把预设渲染成 messages。

    参数:
        parsed: 已解析（并已应用覆盖层）的预设；
        context: 运行时内容；
        memory_injection: 记忆注入配置；缺省用 `parsed.memory_injection`
            （store.load 已填好默认值）。
    """
    preset = parsed.preset
    warnings: list[str] = list(parsed.warnings)
    memory_cfg = normalize_memory_injection(
        memory_injection if memory_injection is not None else parsed.memory_injection
    )

    # ---- ① 记忆注入：落在世界书槽位时，先并入对应内容 ----
    blocks = memory_cfg["enabled"] and bool(context.memory_text.strip())
    if blocks and memory_cfg["position"] == "world_info_before":
        context = replace(context, worldbook_before=_join(context.worldbook_before, context.memory_text))
    elif blocks and memory_cfg["position"] == "world_info_after":
        context = replace(context, worldbook_after=_join(context.worldbook_after, context.memory_text))

    # ---- ② 各条目生效正文（宏替换 + use_sysprompt 覆盖）----
    substitute = _MacroRunner(_build_macro_context(context))
    contents = _resolve_contents(parsed, context, warnings, substitute)

    # ---- ③ 历史块 ----
    history_messages = _build_history(context, preset, warnings, substitute)

    # ---- ④ In-Chat 注入 ----
    history_messages, in_chat_count = _apply_in_chat(
        parsed, context, memory_cfg, contents, history_messages
    )
    history_placed = False
    style_text = context.style_text.strip()
    style_placed = False

    # ---- ⑤ 相对条目顺序展开 ----
    messages: list[dict[str, str]] = []
    used_markers: list[str] = []
    empty_markers: list[str] = []
    example_count = 0

    for item, enabled in parsed.ordered_items:
        if not enabled or not _trigger_allows(item, context.generation_type):
            continue
        if item.is_in_chat:
            continue   # 已在 ④ 处理

        identifier = item.identifier

        if identifier == MARKER_CHAT_HISTORY:
            messages.extend(history_messages)
            used_markers.append(identifier)
            history_placed = True
            continue

        if identifier == MARKER_DIALOGUE_EXAMPLES:
            example_messages = _build_examples(context, preset, substitute)
            if example_messages:
                messages.extend(example_messages)
                used_markers.append(identifier)
                example_count = len(context.examples)
            else:
                empty_markers.append(identifier)
            continue

        if identifier in TEXT_MARKERS:
            text, marker_warnings = fill_text_marker(
                identifier, contents.get(identifier, item.content), context, preset
            )
            warnings.extend(marker_warnings)
            text = substitute(text)
            if not text.strip():
                empty_markers.append(identifier)
                continue
            used_markers.append(identifier)
            messages.append({"role": item.role, "content": text.strip()})
            continue

        # 自带正文条目（main / nsfw / jailbreak / enhanceDefinitions / 普通条目）
        text = contents.get(identifier, item.content)
        if identifier == MARKER_JAILBREAK and style_text:
            # 文风层追加在尾部指令之后，不覆盖预设正文（契约文档 §6.6）
            text = _join(text, style_text)
            style_placed = True
        if not text.strip():
            continue
        messages.append({"role": item.role, "content": text.strip()})

    # 预设未启用 chatHistory 槽位时的容错：历史不能凭空消失
    if not history_placed and history_messages:
        warnings.append("预设未启用 chatHistory 槽位，对话历史已追加到消息末尾")
        messages.extend(history_messages)

    # ---- ⑥ 记忆注入的 in_prompt 模式（等价于 ST 的 IN_PROMPT = 'end'）----
    if blocks and memory_cfg["position"] == "in_prompt":
        messages.append({"role": memory_cfg["role"], "content": context.memory_text.strip()})

    # 文风层没找到 jailbreak 槽位时的容错：追加到末尾，不丢用户选的文风
    if style_text and not style_placed:
        warnings.append("预设未启用 jailbreak 槽位，文风块已追加到消息末尾")
        messages.append({"role": "system", "content": style_text})

    # ---- ⑦ 合并相邻 system 消息 ----
    if preset.squash_system_messages:
        messages = _squash_system_messages(messages)

    if substitute.unresolved:
        warnings.append(
            "以下宏在本项目未实现，已保留原文：" + "、".join(substitute.unresolved)
        )

    return RenderedPrompt(
        messages=messages,
        warnings=warnings,
        used_markers=used_markers,
        empty_markers=empty_markers,
        in_chat_count=in_chat_count,
        example_count=example_count,
        unresolved_macros=list(substitute.unresolved),
    )


# --------------------------------------------------------------------------
# 内部实现
# --------------------------------------------------------------------------


def _join(first: str, second: str) -> str:
    """用空行拼接两段文本（任一段为空时返回另一段）。"""
    parts = [part.strip() for part in (first, second) if part and part.strip()]
    return "\n\n".join(parts)


def _trigger_allows(item, generation_type: str) -> bool:
    """生成类型触发过滤：空列表 = 所有类型都发送。"""
    if not item.injection_trigger:
        return True
    return generation_type in item.injection_trigger


def _resolve_contents(
    parsed: ParsedPreset,
    context: STRenderContext,
    warnings: list[str],
    substitute: "_MacroRunner",
) -> dict[str, str]:
    """计算每个条目生效的正文（宏替换 + use_sysprompt 的 System Prompt 覆盖）。

    覆盖规则（契约文档 §6.4）：仅当 `use_sysprompt` 开启、覆盖文本非空时，
    对 `system_prompt=True` 且 `forbid_overrides=False` 的非 marker 条目替换正文。
    """
    preset = parsed.preset
    override = context.system_prompt_override.strip() if preset.use_sysprompt else ""
    contents: dict[str, str] = {}

    for item in preset.prompts:
        content = item.content
        if override and item.system_prompt and not item.forbid_overrides and not item.marker:
            content = override
        contents[item.identifier] = substitute(content)

    if preset.use_sysprompt and not override:
        warnings.append("预设开启了 System Prompt 覆盖，但未提供覆盖文本，已按原正文发送")

    return contents


def _build_history(
    context: STRenderContext,
    preset,
    warnings: list[str],
    substitute: "_MacroRunner",
) -> list[dict[str, str]]:
    """构建历史块：空会话分隔文本 + 历史消息 + 本次输入。

    历史消息正文**不过宏**：那是已发生的对话原文，改写它会与界面上展示的
    聊天记录不一致（预设产生的文本才需要宏替换）。
    """
    messages: list[dict[str, str]] = []

    if not context.history and preset.new_chat_prompt.strip():
        messages.append(
            {"role": "system", "content": substitute(preset.new_chat_prompt.strip())}
        )

    with_name_prefix = preset.names_behavior == 1
    for turn in context.history:
        content = turn.text
        if with_name_prefix:
            name = context.persona_name if turn.role == "assistant" else context.user_name
            if name:
                content = f"{name}: {content}"
        if content.strip():
            messages.append({"role": turn.role, "content": content})

    user_text = context.user_input.strip()
    if not user_text:
        # 空输入：ST 会用 send_if_empty 的文本代替（未设置则不发送）
        fallback = preset.send_if_empty.strip()
        if fallback:
            warnings.append("本次输入为空，已使用预设的「空消息替代文本」")
            user_text = substitute(fallback)
    if user_text:
        messages.append({"role": "user", "content": user_text})

    return messages


def _build_examples(
    context: STRenderContext,
    preset,
    substitute: "_MacroRunner",
) -> list[dict[str, str]]:
    """构建 few-shot 示例块（示例对话之前可插入预设的示例分隔文本）。"""
    if not context.examples:
        return []

    messages: list[dict[str, str]] = []
    if preset.new_example_chat_prompt.strip():
        messages.append(
            {"role": "system", "content": substitute(preset.new_example_chat_prompt.strip())}
        )

    for user_text, assistant_text in context.examples:
        if user_text.strip():
            messages.append({"role": "user", "content": substitute(user_text.strip())})
        if assistant_text.strip():
            messages.append({"role": "assistant", "content": substitute(assistant_text.strip())})
    return messages


def _apply_in_chat(
    parsed: ParsedPreset,
    context: STRenderContext,
    memory_cfg: dict,
    contents: dict[str, str],
    base_messages: list[dict[str, str]],
) -> tuple[list[dict[str, str]], int]:
    """把 In-Chat 条目与记忆扩展注入按 depth 插进历史块。

    depth 语义（对齐 ST）：0 = 最后一条消息之后；1 = 最后一条之前；以此类推。
    同 depth 内先按 injection_order 升序分组，再把同组同 role 的正文用换行合并成一条消息。
    """
    items: list[_InChatItem] = []

    for item, enabled in parsed.ordered_items:
        if not enabled or not item.is_in_chat:
            continue
        if not _trigger_allows(item, context.generation_type):
            continue
        text = contents.get(item.identifier, item.content).strip()
        if not text:
            continue
        items.append(
            _InChatItem(
                content=text,
                role=item.role,
                depth=max(0, item.injection_depth),
                order=item.injection_order,
            )
        )

    # HyPRA 记忆块作为扩展注入参与同层合并（契约文档 §7）
    if (
        memory_cfg["enabled"]
        and memory_cfg["position"] == "in_chat"
        and context.memory_text.strip()
    ):
        items.append(
            _InChatItem(
                content=context.memory_text.strip(),
                role=memory_cfg["role"],
                depth=memory_cfg["depth"],
                order=memory_cfg["order"],
            )
        )

    if not items:
        return base_messages, 0

    base_length = len(base_messages)
    by_depth: dict[int, list[_InChatItem]] = defaultdict(list)
    for item in items:
        by_depth[item.depth].append(item)

    # 锚点按基准数组计算（不随插入漂移）：depth 越大越靠前；同为 0 时大 depth 在前
    anchors: list[tuple[int, int, list[dict[str, str]]]] = []
    for depth, depth_items in by_depth.items():
        anchors.append((max(0, base_length - depth), -depth, _group_to_messages(depth_items)))
    anchors.sort(key=lambda anchor: (anchor[0], anchor[1]))

    result: list[dict[str, str]] = []
    previous = 0
    inserted = 0
    for index, _neg_depth, group_messages in anchors:
        result.extend(base_messages[previous:index])
        result.extend(group_messages)
        inserted += len(group_messages)
        previous = index
    result.extend(base_messages[previous:])

    return result, inserted


def _build_macro_context(context: STRenderContext) -> MacroContext:
    """由渲染上下文构造宏上下文（变量字典按引用传入，setvar 才能落到会话上）。"""
    return MacroContext(
        char_name=context.persona_name,
        user_name=context.user_name,
        description=context.persona_text,
        personality=context.persona_personality,
        scenario=context.scenario_text,
        persona=context.user_persona,
        variables=context.macro_variables,
    )


class _MacroRunner:
    """宏解析的统一入口：一处调用、一处收集未识别宏，避免各分支写法不一致。"""

    def __init__(self, context: MacroContext) -> None:
        self._context = context
        self.unresolved: list[str] = []

    def __call__(self, text: str) -> str:
        resolved, unresolved = render_macros(text, self._context)
        for macro in unresolved:
            if macro not in self.unresolved:
                self.unresolved.append(macro)
        return resolved


@dataclass
class _InChatItem:
    """一个待注入的 In-Chat 片段（预设条目或记忆扩展注入）。"""

    content: str
    role: str
    depth: int
    order: int


def _group_to_messages(items: list[_InChatItem]) -> list[dict[str, str]]:
    """同一 depth 的片段 → 消息列表（先按 order 分组，组内按角色合并）。"""
    by_order: dict[int, list[_InChatItem]] = defaultdict(list)
    for item in items:
        by_order[item.order].append(item)

    messages: list[dict[str, str]] = []
    for order in sorted(by_order):
        by_role: dict[str, list[str]] = defaultdict(list)
        for item in by_order[order]:
            by_role[item.role].append(item.content)
        for role in _ROLE_ORDER:
            parts = [text for text in by_role.get(role, []) if text.strip()]
            if parts:
                messages.append({"role": role, "content": "\n".join(parts)})
    return messages


def _squash_system_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """合并相邻的无 name 的 system 消息（对齐 ST 的 squashSystemMessages）。"""
    squashed: list[dict[str, str]] = []
    for message in messages:
        previous = squashed[-1] if squashed else None
        if (
            previous is not None
            and previous.get("role") == "system"
            and message.get("role") == "system"
            and "name" not in previous
            and "name" not in message
        ):
            squashed[-1] = {
                "role": "system",
                "content": f"{previous['content']}\n{message['content']}",
            }
            continue
        squashed.append(dict(message))
    return squashed
