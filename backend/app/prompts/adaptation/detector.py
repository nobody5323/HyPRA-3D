"""预设适配的规则层：确定性体检 + 确定性修复。

职责边界（本模块**不调用模型**）：

1. 按 `rules.yaml` 扫描预设，产出结构化问题清单（`Finding`）；
2. 对「规则层就能确定修好」的项，直接生成覆盖层补丁（`auto_patch`）；
3. 把需要语义改写的条目录成 `RewriteTask`，交给 planner 一次调用集中处理。

为什么分两层：一份预设动辄上百条、正文上万字，全部交模型既贵又容易漏；而
「阈值超标」「宏里写了思考标签」「字数要求过大」这类是确定性判断，不该占用
模型调用。实测常见预设（如第三人称小说预设）里，一半以上的问题是确定性的。

保护优先级（用户明确要求）：命中 `preserve` 规则（NSFW / 破限）的条目
**不进入模型改写清单**，内容保持原样；但两件事照做：
- 确定性的格式清理（如清掉宏里的思考标签）——它只动标签片段，不触碰创作语义；
- 位置归拢——把这类条目移到同语义槽位（jailbreak / nsfw）之后，只动顺序。
"""

from __future__ import annotations

import re
from typing import Any

from app.prompts.adaptation.loader import load_rules
from app.prompts.adaptation.models import (
    AdaptationRules,
    ContentRule,
    DetectionResult,
    Finding,
    PresetRule,
    RewriteTask,
)
from app.prompts.st_compat import RUNTIME_FILLED_MARKERS, ParsedPreset

#: 证据片段的最大长度（界面展示用，避免把整段提示词带进日志/响应）
_EVIDENCE_LIMIT = 80

#: 预设字段 → 目标上限的取值函数（clamp_int 用）
_CLAMP_TARGETS = {
    "openai_max_tokens": lambda target: target.max_tokens,
    "openai_max_context": lambda target: target.max_context,
}


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def detect(parsed: ParsedPreset, rules: AdaptationRules | None = None) -> DetectionResult:
    """体检一份已解析的 ST 预设，返回问题清单与确定性补丁。

    参数:
        parsed: `StPresetStore.load()` 的结果（已应用覆盖层）；
        rules: 规则集，缺省用内置 rules.yaml。
    """
    active = rules or load_rules()
    result = DetectionResult()

    _scan_preset_fields(parsed, active, result)
    _scan_entries(parsed, active, result)
    _drop_disabled_from_rewrite_tasks(result)
    _plan_preserved_order(parsed, result)

    return result


def _drop_disabled_from_rewrite_tasks(result: DetectionResult) -> None:
    """已被关闭的条目不必再送模型改写：改了也不会组装进提示词，白花 token。

    典型场景：一条条目既命中 `disable_entry`（整条是模板，直接关掉），
    又命中 `llm_rewrite`（里面含特殊 token）——两者只取前者。
    """
    disabled = {
        identifier
        for identifier, fields in (result.auto_patch.get("prompts") or {}).items()
        if isinstance(fields, dict) and fields.get("enabled") is False
    }
    if not disabled:
        return
    result.rewrite_tasks = [
        task for task in result.rewrite_tasks if task.identifier not in disabled
    ]


def _plan_preserved_order(parsed: ParsedPreset, result: DetectionResult) -> None:
    """把 preserve 类条目（NSFW / 破限）归拢到同语义槽位之后。

    用户明确要求：这两类条目**内容保持原样**，但要放到「合适的位置」。
    本函数只动顺序，不动正文与开关：把命中 preserve 且有 `anchor` 的条目
    移动到对应槽位（如 `jailbreak` / `nsfw`）之后，它们之间的相对顺序不变，
    其余条目顺序也不变。

    槽位不存在、条目本来就已在位置上时，不写 `prompt_order` 补丁。
    """
    order_ids = [entry.identifier for entry in parsed.order]
    if not order_ids:
        return

    moves: dict[str, list[str]] = {}
    moved: set[str] = set()
    for finding in result.preserved:
        anchor = finding.anchor
        identifier = finding.identifier
        # 去重：同一条目可能命中多条 preserve 规则；槽位或条目不存在则跳过
        if not anchor or identifier in moved or anchor == identifier:
            continue
        if anchor not in order_ids or identifier not in order_ids:
            continue
        moved.add(identifier)
        moves.setdefault(anchor, []).append(identifier)

    if not moves:
        return

    arranged = [identifier for identifier in order_ids if identifier not in moved]

    # 逐个锚点归位。为什么要多轮：锚点自己也可能被移动（两类保护互为锚点，
    # 例如破限条目正文里带了 NSFW 关键词）——它被剔出 arranged 后当场定位不到，
    # 得等它被别人的移动带进来再处理。单轮直接 `arranged.index(anchor)` 会抛
    # ValueError（接口 500），而单轮直接 `continue` 则会把该锚点下的条目漏掉。
    pending = dict(moves)
    while pending:
        progressed = False
        for anchor in list(pending):
            if anchor not in arranged:
                continue
            position = arranged.index(anchor) + 1
            arranged[position:position] = pending.pop(anchor)
            progressed = True
        if not progressed:
            # 剩下的是锚点不在顺序表里的异常情形：追加到末尾而不丢弃，
            # 否则这些条目会从顺序里消失（宁可不归拢，也不能丢条目）
            for identifiers in pending.values():
                arranged.extend(identifiers)
            break

    # 位置本来就对：不写补丁，避免界面上出现无意义的「已调整顺序」
    if arranged != order_ids:
        result.auto_patch["prompt_order"] = arranged


# --------------------------------------------------------------------------
# 预设级：判定顶层字段
# --------------------------------------------------------------------------


def _preset_field(preset: Any, field: str) -> Any:
    """读取预设字段；声明字段与 extra 字段（如 show_thoughts）都能取到。"""
    value = getattr(preset, field, None)
    if value is None:
        extra = getattr(preset, "model_extra", None) or {}
        return extra.get(field)
    return value


def _scan_preset_fields(
    parsed: ParsedPreset, rules: AdaptationRules, result: DetectionResult
) -> None:
    """逐条判定预设级规则（阈值/开关）。"""
    for rule in rules.preset_rules:
        value = _preset_field(parsed.preset, rule.field)
        if value is None or not rule.matches(value):
            continue

        finding = Finding(
            rule_id=rule.id,
            severity=rule.severity,
            action=rule.action,
            scope="preset",
            identifier="",
            name=rule.field,
            evidence=f"{rule.field} = {value}",
            message=rule.message,
        )
        result.findings.append(finding)

        if rule.action == "auto_patch":
            _apply_fix(rule, finding, parsed, rules, result.auto_patch)


# --------------------------------------------------------------------------
# 条目级：判定条目正文
# --------------------------------------------------------------------------


def _scan_entries(
    parsed: ParsedPreset, rules: AdaptationRules, result: DetectionResult
) -> None:
    """只扫**已启用**条目：禁用的条目不参与组装，改了也不影响本轮行为。

    占位条目（marker / 运行时填充）正文为空且不可覆盖，直接跳过。
    """
    for item in parsed.enabled_items:
        if item.is_marker or item.identifier in RUNTIME_FILLED_MARKERS:
            continue
        content = item.content or ""
        if not content.strip():
            continue

        hits = [rule for rule in rules.content_rules if re.search(rule.regex, content)]
        if not hits:
            continue

        # 保护优先级：命中 preserve 的条目不做内容改写（内容原样保留）
        protected = any(rule.action == "preserve" for rule in hits)
        task: RewriteTask | None = None

        for rule in hits:
            finding = Finding(
                rule_id=rule.id,
                severity=rule.severity,
                action=rule.action,
                scope="content",
                identifier=item.identifier,
                name=item.display_name,
                evidence=_clip(_match_text(rule, content)),
                message=rule.message,
                anchor=rule.anchor,
            )
            result.findings.append(finding)

            if rule.action == "preserve":
                result.preserved.append(finding)
            elif rule.action == "auto_patch":
                # 保护优先级也涵盖「开关」：契约是「内容与开关都不动」。
                # 确定性格式清理（只动标签片段）仍执行，但停在关掉整个条目。
                if protected and getattr(rule, "fix", "") == "disable_entry":
                    result.warnings.append(
                        f"条目「{item.display_name}」命中保护规则（NSFW / 破限），"
                        "按约定不改变它的开关；如确需关闭请手动操作"
                    )
                else:
                    _apply_fix(rule, finding, parsed, rules, result.auto_patch)
            elif rule.action == "llm_rewrite" and not protected:
                if task is None:
                    task = RewriteTask(
                        identifier=item.identifier,
                        name=item.display_name,
                        content=content,
                    )
                task.add_issue(rule.id, rule.message)

        if task is not None:
            result.rewrite_tasks.append(task)


# --------------------------------------------------------------------------
# 确定性修复（fix 分发）
# --------------------------------------------------------------------------


def _apply_fix(
    rule: PresetRule | ContentRule,
    finding: Finding,
    parsed: ParsedPreset,
    rules: AdaptationRules,
    patch: dict[str, Any],
) -> None:
    """按 `fix` 标识生成覆盖层补丁。

    补丁结构必须与 `st_compat.store.apply_override` 的语义一致，
    否则会出现「接口返回成功但预设没变」——这是本层最容易踩的坑。
    """
    fix = getattr(rule, "fix", "")
    if fix == "disable_thinking":
        # assembly 通道（show_thoughts 是 STPreset 的 extra 字段，不走 sampling）
        patch.setdefault("assembly", {})["show_thoughts"] = False

    elif fix == "clamp_int":
        target = _CLAMP_TARGETS.get(rule.field)  # type: ignore[union-attr]
        if target is not None:
            patch.setdefault("sampling", {})[rule.field] = target(rules.target)  # type: ignore[union-attr]

    elif fix == "disable_entry":
        patch.setdefault("prompts", {}).setdefault(finding.identifier, {})["enabled"] = False

    elif fix == "clear_thinking_macro":
        content = _working_content(patch, parsed, finding.identifier)
        cleaned = re.sub(rule.regex, "setvar::cotTitle::", content)  # type: ignore[union-attr]
        _set_content(patch, finding.identifier, cleaned)

    elif fix == "clamp_reply_length":
        content = _working_content(patch, parsed, finding.identifier)
        clamped = _sub_last_group(rule.regex, rules.target.reply_length, content)  # type: ignore[union-attr]
        _set_content(patch, finding.identifier, clamped)

    elif fix == "flip_reply_length_bound":
        content = _working_content(patch, parsed, finding.identifier)
        flipped = _flip_reply_bound(rule.regex, rules.target.reply_length, content)  # type: ignore[union-attr]
        _set_content(patch, finding.identifier, flipped)


def _working_content(patch: dict[str, Any], parsed: ParsedPreset, identifier: str) -> str:
    """取条目的「当前」正文：优先用补丁里已改过的版本，避免多次修复互相覆盖。"""
    pending = patch.get("prompts", {}).get(identifier, {})
    if "content" in pending:
        return pending["content"]
    item = parsed.get(identifier)
    return item.content if item is not None else ""


def _set_content(patch: dict[str, Any], identifier: str, content: str) -> None:
    """把改写后的正文写进补丁。"""
    patch.setdefault("prompts", {}).setdefault(identifier, {})["content"] = content


def _sub_last_group(pattern: str, value: object, text: str) -> str:
    """把匹配串里**最后一个捕获组**换成新值，保留其余原文。

    用于「字数上限」这类只想改数字的修复：正则同时捕获了前缀词（不超过 / 至多）
    与数字，只有替换数字才能保留原文语气。
    """
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        if not match.groups():
            return match.group(0)
        group_index = len(match.groups())
        start, end = match.span(group_index)
        # match 的 span 是相对**原始** text 的索引，因此这里的切片是安全的
        return text[match.start() : start] + str(value) + text[end : match.end()]

    return re.sub(pattern, _replace, text)


def _flip_reply_bound(pattern: str, target: int, text: str) -> str:
    """把「不少于 N 字」这类**下限**要求翻转成「不超过 M 字」的上限要求。

    为什么要翻转而不是只改数字：故事创作的「不少于 2500 字」在陪伴对话里
    方向是反的。保留「不少于」的话，把数字从 2500 钳到 300 也白搭——
    实测模型照样写 240 字（因为指令仍然是「不许短」），而改成上限后降到 24~47 字。
    """
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        # 保留限定词之前的原文（如 `setvar::wordsCloud::`），只换掉限定词与数字
        if not match.groups():
            return f"不超过{target}"
        return text[match.start() : match.start(1)] + f"不超过{target}"

    return re.sub(pattern, _replace, text)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _match_text(rule: ContentRule, content: str) -> str:
    """取规则命中的原文片段（用于证据展示）。"""
    match = re.search(rule.regex, content)
    return match.group(0) if match else ""


def _clip(text: str, limit: int = _EVIDENCE_LIMIT) -> str:
    """截断过长证据，保留可读的前缀。"""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
