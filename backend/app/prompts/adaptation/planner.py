"""预设适配的规划层：把「需要语义改写」的条目攒成**一次**模型调用。

为什么一次调用而不是逐条：

- 逐条调用 N 次，延迟与费用随条目数线性增长（这个预设就有 4 条待改写）；
- 一次给全，模型还能保持条目间的口径一致（同为「陪伴对话」语气）。

安全阀（宁可不做，也不做坏）：

- 模型返回必须是严格 JSON；解析失败 → 整批放弃，只保留规则层的确定性补丁；
- identifier 必须是本次请求过的条目（防止模型幻觉出新条目，或改到 marker）；
- 空内容直接拒绝；
- 改写幅度过大 → 仍然采纳但**标注告警**（用户会在 diff 界面上看到「这条改动较大」），
  因为「删掉伪对话外壳」这类正当改动本来就会大幅缩短正文，一刀切拒绝会卡住正常流程；
- 模型调用失败 → 降级为「只应用确定性修复」，不抛异常（与项目的 MCP 降级风格一致）。
"""

from __future__ import annotations

import json
import re
from copy import deepcopy

from app.llm.base import ChatMessage, LLMProvider
from app.prompts.adaptation.loader import load_rules
from app.prompts.adaptation.models import (
    AdaptationRules,
    DetectionResult,
    PlanResult,
    RewriteOutcome,
    RewriteTask,
)
from app.prompts.st_compat import ParsedPreset

#: 改写用低温度：这项任务要的是稳定执行，不是文采
_REWRITE_TEMPERATURE = 0.2
_REWRITE_MAX_TOKENS = 4096

#: 幅度检查只对足够长的条目生效（短条目本就容易 100% 变化）
_MIN_LEN_FOR_RATIO_CHECK = 80
#: 超过该变化比例就标注告警（不拒绝，交用户在 diff 里判断）
_MAX_CHANGE_RATIO = 0.6

#: 否定词：用于区分「要求这件事」与「禁止这件事」
_NEGATION_WORDS = (
    "不要",
    "不得",
    "不应",
    "不再",
    "禁止",
    "避免",
    "无需",
    "不用",
    "切勿",
    "别",
    "勿",
)

#: 人称指令（按 target.person 取值；未知取值回退第二人称）
_PERSON_INSTRUCTIONS = {
    "second_person": (
        "AI 以第一人称自称「我」，用第二人称「你」称呼用户；"
        "写的是「对用户说的话」，不是第三人称小说旁白"
    ),
    "first_person": "AI 以第一人称「我」叙述，用户以「你」被提及",
    "third_person": "保留第三人称叙事，但语气改为对用户说话，而不是写小说",
}


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def plan(
    parsed: ParsedPreset,
    detection: DetectionResult,
    llm: LLMProvider | None,
    rules: AdaptationRules | None = None,
) -> PlanResult:
    """合成最终覆盖层补丁：规则层确定性修复 ⊕ 模型条目改写。

    参数:
        parsed: 已解析的预设（`StPresetStore.load()` 的结果）；
        detection: 规则层的体检结果（`detect()` 的产出）；
        llm: 模型提供方；为 None 时跳过改写，只返回确定性补丁；
        rules: 规则集，缺省用内置 rules.yaml。
    """
    active = rules or load_rules()
    result = PlanResult(
        patch=deepcopy(detection.auto_patch),
        findings=list(detection.findings),
        preserved=list(detection.preserved),
        # 规则层的提示也要传下去（否则界面看不到「因保护规则而未关闭某条目」）
        warnings=list(detection.warnings),
    )

    tasks = list(detection.rewrite_tasks)
    if not tasks:
        return result

    if llm is None:
        _mark_all(result, tasks, "skipped", "未配置可用模型")
        result.warnings.append("未配置可用模型，已跳过条目改写（仅应用确定性修复）")
        return result

    try:
        raw = llm.chat(
            _build_messages(tasks, active),
            temperature=_REWRITE_TEMPERATURE,
            max_tokens=_REWRITE_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 —— 模型侧异常不应打断适配流程
        _mark_all(result, tasks, "skipped", "模型调用失败")
        result.warnings.append(f"模型调用失败，已跳过条目改写：{exc}")
        return result

    result.model_used = True
    payload = _extract_json(raw)
    if payload is None:
        _mark_all(result, tasks, "skipped", "模型返回不是合法 JSON")
        result.warnings.append("模型返回不是合法 JSON，已跳过全部条目改写")
        return result

    _apply_items(result, tasks, payload, active)
    return result


def _mark_all(
    result: PlanResult, tasks: list[RewriteTask], status: str, reason: str
) -> None:
    """把整批条目标记为同一处置（降级路径用）。"""
    result.rewrites.extend(
        RewriteOutcome(
            identifier=task.identifier,
            name=task.name,
            status=status,
            reason=reason,
        )
        for task in tasks
    )


def _apply_items(
    result: PlanResult,
    tasks: list[RewriteTask],
    payload: dict,
    rules: AdaptationRules,
) -> None:
    """校验并采纳模型返回的改写条目。

    只接受「本次请求过的 identifier」——这既是防幻觉，也是防模型误改 marker
    （marker 条目根本不在 tasks 里，自然被挡在门外）。
    """
    requested = {task.identifier: task for task in tasks}
    items = payload.get("items")
    if not isinstance(items, list):
        _mark_all(result, tasks, "skipped", "模型返回缺少 items 列表")
        result.warnings.append("模型返回缺少 items 列表，已跳过全部条目改写")
        return

    handled: set[str] = set()
    for entry in items:
        if not isinstance(entry, dict):
            continue
        identifier = str(entry.get("identifier") or "").strip()
        task = requested.get(identifier)
        if task is None:
            result.warnings.append(f"模型返回了未请求的条目「{identifier}」，已忽略")
            continue
        if identifier in handled:
            continue
        handled.add(identifier)

        new_content = str(entry.get("content") or "")
        status, note = _evaluate(task, new_content, rules)

        if status == "rejected":
            result.rewrites.append(
                RewriteOutcome(identifier, task.name, "rejected", note)
            )
            result.warnings.append(f"条目「{task.name}」改写被拒绝：{note}")
            continue

        result.patch.setdefault("prompts", {}).setdefault(identifier, {})[
            "content"
        ] = new_content
        result.rewrites.append(
            RewriteOutcome(identifier, task.name, "applied", note, new_content)
        )
        if note:
            result.warnings.append(f"条目「{task.name}」{note}")

    for task in tasks:
        if task.identifier in handled:
            continue
        result.rewrites.append(
            RewriteOutcome(task.identifier, task.name, "missing", "模型未返回该条目")
        )
        result.warnings.append(f"条目「{task.name}」模型未返回，保持原文")


def _evaluate(
    task: RewriteTask, new_content: str, rules: AdaptationRules
) -> tuple[str, str]:
    """评估一条改写是否可采纳，返回 (status, note)。

    `note` 在拒绝时是原因，在采纳时是提示（展示给用户「这条改动较大，请重点检查」）。
    """
    if not new_content.strip():
        return "rejected", "模型返回的正文为空"

    if new_content.strip() == task.content.strip():
        return "rejected", "模型未做任何改动"

    residual = _residual_issues(task, new_content, rules)
    if residual:
        # 改完还是原样子：这次改写没达到目的，保持用户自己的原文更安全
        # （不留一个「看着改过、实际问题还在」的半成品）
        return "rejected", f"改写后仍存在原问题（{residual[0]}）"

    old_len = len(task.content)
    if old_len >= _MIN_LEN_FOR_RATIO_CHECK:
        ratio = abs(len(new_content) - old_len) / old_len
        if ratio > _MAX_CHANGE_RATIO:
            return "applied", f"改动幅度较大（{ratio:.0%}），建议重点检查"

    return "applied", ""


def _residual_issues(
    task: RewriteTask, content: str, rules: AdaptationRules
) -> list[str]:
    """复查改写后的正文是否仍命中当初判定的问题。

    只复查本条目当初命中的那几条规则（不是全量重扫）：既能抓住「敷衍改写」，
    又不会因为内容变化引入了别的规则命中而误拒。

    **否定句不算残留**：模型常把条目改成「不得使用第三人称」「不要写动作、神态」——
    关键词还在，但那恰恰是我们要的写法。实测不看否定词会让这两条改写被误拒，
    旧内容（要求第三人称 + 引号对白）保留，回复反而变回小说格式。
    """
    by_id = {rule.id: rule for rule in rules.content_rules}
    residual: list[str] = []
    for rule_id in task.rule_ids:
        rule = by_id.get(rule_id)
        if rule is None:
            continue
        try:
            match = re.search(rule.regex, content)
        except re.error:      # 规则文件已在加载时校验过，这里只做防御
            continue
        if match is None or _is_negated(content, match.start()):
            continue
        residual.append(rule.message)
    return residual


def _is_negated(text: str, position: int, window: int = 10) -> bool:
    """命中位置**之前**一小段里是否有否定词。

    只看前不看后：中文的否定几乎都是前置的（「不要写动作」「不得使用第三人称」）。
    """
    head = text[max(0, position - window) : position]
    return any(word in head for word in _NEGATION_WORDS)


# --------------------------------------------------------------------------
# 提示词组装
# --------------------------------------------------------------------------


def _build_messages(tasks: list[RewriteTask], rules: AdaptationRules) -> list[ChatMessage]:
    """组装 meta-prompt：适配目标 + 待改条目 + 严格的输出格式要求。"""
    target = rules.target
    person = _PERSON_INSTRUCTIONS.get(target.person, _PERSON_INSTRUCTIONS["second_person"])

    # 输出形态：`dialogue_only` 是主导项 —— 小说预设的条目会要求写动作/环境/心理描写，
    # 不把这条说清楚，模型会把「以可见行动结尾」之类的要求原样保留。
    output_rule = (
        "- 输出形态：只输出角色说的**话本身**；不要写动作、神情、环境或心理描写，"
        "也不要输出小说结构标签、思考过程或固定模板"
        if target.dialogue_only
        else "- 输出：不要输出小说结构标签、不要输出思考过程、不要按固定模板排版"
    )

    header = "\n".join(
        [
            "# 适配目标",
            f"- 人称：{person}",
            output_rule,
            f"- 长度：单次回复不超过 {target.reply_length} 字",
            "- 身份：AI 就是角色本人，正在与用户对话；不是「帮用户写作的创作者」",
            "",
            f"# 待改写条目（共 {len(tasks)} 条）",
        ]
    )

    blocks = [header]
    for index, task in enumerate(tasks, start=1):
        issues = "\n".join(f"- {issue}" for issue in task.issues)
        blocks.append(
            "\n".join(
                [
                    f"\n## 条目 {index}",
                    f"identifier: {task.identifier}",
                    f"名称: {task.name}",
                    "问题:",
                    issues,
                    "原文:",
                    task.content,
                ]
            )
        )

    blocks.append(
        "\n".join(
            [
                "",
                "# 输出格式（严格遵守）",
                "只输出一个 JSON 对象，不要任何解释、前后缀或代码块标记：",
                '{"items": [{"identifier": "条目 identifier", "content": "改写后的完整正文"}]}',
                "硬性要求：",
                "1. identifier 必须与上面给出的完全一致，不得新增或删除条目；",
                "2. content 是该条目的**完整新正文**，不是差异片段、不是摘要；",
                "3. 只修改与上面「问题」相关的部分；其余内容（角色设定、创作自由度、",
                "   内容分级等表述）逐字保留，不要顺手「润色」；",
                "4. 正文里不得出现 <｜…｜> 形式的特殊 token，也不得出现 <thinking> 标签；",
                "5. 改写后的条目不得再要求写动作、神情、环境或心理描写，也不得要求",
                "   把描写穿插进对白。",
            ]
        )
    )

    return [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(role="user", content="\n".join(blocks)),
    ]


_SYSTEM_PROMPT = (
    "你是「提示词预设适配助手」。用户从别处导入了一份面向小说创作 / 角色扮演的预设，"
    "需要把它调整成「一对一陪伴对话」形态，同时尽可能保留作者原本的创作意图。\n"
    "你的唯一任务是：按用户给出的问题清单改写指定条目的正文，然后输出 JSON。"
)


# --------------------------------------------------------------------------
# 返回解析
# --------------------------------------------------------------------------


def _extract_json(text: str) -> dict | None:
    """从模型输出里抽出 JSON 对象（容忍代码块围栏与前后缀说明）。"""
    if not text:
        return None

    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None

    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
