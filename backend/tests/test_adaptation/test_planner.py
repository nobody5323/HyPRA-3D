"""预设适配规划层的测试（一次模型调用 + 严格校验 + 降级）。"""

import json

from app.llm.base import ChatMessage, LLMProvider
from app.prompts.adaptation import detect, plan


class FakeLLM(LLMProvider):
    """测试替身：返回预设好的文本（或抛异常），并记录收到的消息。"""

    name = "fake"

    def __init__(self, reply: str | Exception = "") -> None:
        self.reply = reply
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> str:
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _items_payload(**items: str) -> str:
    """构造模型返回的 JSON（identifier → 新正文）。"""
    return json.dumps(
        {"items": [{"identifier": k, "content": v} for k, v in items.items()]},
        ensure_ascii=False,
    )


REWRITTEN_PERSON = "（测试语料）你与角色面对面聊天，角色用「你」称呼你。"


# --------------------------------------------------------------------------
# 正常路径
# --------------------------------------------------------------------------


def test_plan_applies_valid_rewrites(make_parsed) -> None:
    """模型返回合法 JSON 时，条目改写被采纳并记入处置记录。"""
    parsed = make_parsed()
    detection = detect(parsed)
    llm = FakeLLM(_items_payload(**{"person-rule": REWRITTEN_PERSON}))

    result = plan(parsed, detection, llm)

    assert result.model_used is True
    assert result.patch["prompts"]["person-rule"]["content"] == REWRITTEN_PERSON
    outcome = next(item for item in result.rewrites if item.identifier == "person-rule")
    assert outcome.status == "applied"
    assert result.applied_count == 1


def test_plan_keeps_deterministic_fixes(make_parsed) -> None:
    """模型改写不得覆盖规则层的确定性修复（两者是合并，不是二选一）。"""
    parsed = make_parsed()
    detection = detect(parsed)

    result = plan(parsed, detection, FakeLLM(_items_payload(**{"person-rule": REWRITTEN_PERSON})))

    assert result.patch["assembly"]["show_thoughts"] is False
    assert result.patch["sampling"]["openai_max_tokens"] == 1024
    assert "{{setvar::wordsCloud::不超过100}}" in result.patch["prompts"]["len-rule"]["content"]


def test_plan_calls_model_exactly_once(make_parsed) -> None:
    """无论多少条待改写，只调用一次模型。"""
    parsed = make_parsed()
    detection = detect(parsed)
    llm = FakeLLM(_items_payload(**{"person-rule": REWRITTEN_PERSON}))

    plan(parsed, detection, llm)

    assert len(llm.calls) == 1


def test_prompt_states_dialogue_only_goal(make_parsed) -> None:
    """dialogue_only 必须体现在 meta-prompt 里。

    不写清楚的话，模型会把「以可见行动结尾」这类要求原样保留，
    回复依旧是小说段落（实测）。
    """
    parsed = make_parsed()
    detection = detect(parsed)
    llm = FakeLLM(_items_payload(**{"person-rule": REWRITTEN_PERSON}))

    plan(parsed, detection, llm)

    user_message = next(m for m in llm.calls[0] if m.role == "user")
    assert "只输出角色说的" in user_message.content
    assert "不要写动作" in user_message.content
    assert "不超过 100 字" in user_message.content


def test_prompt_carries_target_and_source(make_parsed) -> None:
    """提示词要带上适配目标、待改条目 identifier 与原文、输出格式要求。"""
    parsed = make_parsed()
    detection = detect(parsed)
    llm = FakeLLM(_items_payload(**{"person-rule": REWRITTEN_PERSON}))

    plan(parsed, detection, llm)

    user_message = next(m for m in llm.calls[0] if m.role == "user")
    assert "第二人称" in user_message.content
    assert "person-rule" in user_message.content
    assert "第三人称创作" in user_message.content   # 原文片段
    assert "items" in user_message.content          # 输出格式
    # 被保护的条目不得出现在提示词里（内容不动，也不该送给模型看）
    assert "NSFW核心" not in user_message.content


# --------------------------------------------------------------------------
# 校验与拒绝
# --------------------------------------------------------------------------


def test_plan_rejects_empty_content(make_parsed) -> None:
    """空正文直接拒绝，保持原文。"""
    parsed = make_parsed()
    detection = detect(parsed)

    result = plan(parsed, detection, FakeLLM(_items_payload(**{"person-rule": "   "})))

    assert "person-rule" not in (result.patch.get("prompts") or {})
    outcome = next(item for item in result.rewrites if item.identifier == "person-rule")
    assert outcome.status == "rejected"


def test_plan_rejects_unchanged_content(make_parsed) -> None:
    """模型原样返回也不算改写成功。"""
    parsed = make_parsed()
    detection = detect(parsed)
    original = parsed.get("person-rule").content

    result = plan(parsed, detection, FakeLLM(_items_payload(**{"person-rule": original})))

    outcome = next(item for item in result.rewrites if item.identifier == "person-rule")
    assert outcome.status == "rejected"


def test_plan_rejects_rewrite_that_keeps_the_problem(make_parsed) -> None:
    """改完仍带原问题 → 拒绝：不留「看着改过、实际上问题还在」的半成品。"""
    parsed = make_parsed()
    detection = detect(parsed)
    # person-rule 当初的问题是「第三人称」，这里改完依然保留该说法
    keeps_problem = "（测试语料）请用第三人称讲述这位对话者的故事。"

    result = plan(parsed, detection, FakeLLM(_items_payload(**{"person-rule": keeps_problem})))

    outcome = next(item for item in result.rewrites if item.identifier == "person-rule")
    assert outcome.status == "rejected"
    assert "仍存在原问题" in outcome.reason
    assert "person-rule" not in (result.patch.get("prompts") or {})


def test_plan_accepts_rewrite_that_forbids_the_problem(make_parsed) -> None:
    """改写后的条目「禁止」原行为（含否定词）必须采纳，不能算残留。

    回归背景（实测）：模型把条目改成「不得使用第三人称」「不要写动作、神态」，
    关键词仍在，旧实现把它当成「问题没解决」而拒绝 —— 旧内容（要求第三人称）
    被保留，回复反而退回小说对白格式。
    """
    parsed = make_parsed()
    detection = detect(parsed)
    forbidding = "（测试语料）请勿使用第三人称，改用第一人称「我」与第二人称「你」。"

    result = plan(parsed, detection, FakeLLM(_items_payload(**{"person-rule": forbidding})))

    outcome = next(item for item in result.rewrites if item.identifier == "person-rule")
    assert outcome.status == "applied"
    assert result.patch["prompts"]["person-rule"]["content"] == forbidding


def test_plan_ignores_unknown_identifier(make_parsed) -> None:
    """模型幻觉出的条目一律忽略并告警（防越权改到别的条目）。"""
    parsed = make_parsed()
    detection = detect(parsed)

    result = plan(
        parsed,
        detection,
        FakeLLM(_items_payload(**{"person-rule": REWRITTEN_PERSON, "main": "（测试语料）乱改主提示"})),
    )

    assert "main" not in (result.patch.get("prompts") or {})
    assert any("未请求的条目" in warning for warning in result.warnings)


def test_plan_marks_missing_items(make_parsed) -> None:
    """模型漏返回的条目标记为 missing，并保持原文。"""
    parsed = make_parsed()
    detection = detect(parsed)

    result = plan(parsed, detection, FakeLLM(json.dumps({"items": []}, ensure_ascii=False)))

    outcome = next(item for item in result.rewrites if item.identifier == "person-rule")
    assert outcome.status == "missing"
    assert "person-rule" not in (result.patch.get("prompts") or {})


def test_plan_flags_large_change_but_keeps_it(make_parsed) -> None:
    """改动幅度过大时采纳但告警（删掉伪对话外壳这类正当改动本来就会大幅缩短）。"""
    long_text = "（测试语料）" + "全程务必采用第三人称创作。" * 40   # 足够长，触发比例检查
    parsed = make_parsed(
        prompts=[
            {"identifier": "main", "name": "主提示", "role": "system", "content": "（测试语料）照常回应。"},
            {
                "identifier": "person-rule",
                "name": "人称准则",
                "role": "system",
                "content": long_text,
            },
        ],
        prompt_order=[
            {
                "character_id": 100000,
                "order": [
                    {"identifier": "main", "enabled": True},
                    {"identifier": "person-rule", "enabled": True},
                ],
            }
        ],
    )
    detection = detect(parsed)

    result = plan(parsed, detection, FakeLLM(_items_payload(**{"person-rule": "（测试语料）简短改写。"})))

    assert result.patch["prompts"]["person-rule"]["content"] == "（测试语料）简短改写。"
    assert any("改动幅度较大" in warning for warning in result.warnings)


# --------------------------------------------------------------------------
# 降级路径
# --------------------------------------------------------------------------


def test_plan_without_llm_applies_deterministic_only(make_parsed) -> None:
    """没有可用模型时，仍然交付确定性修复。"""
    parsed = make_parsed()
    detection = detect(parsed)

    result = plan(parsed, detection, None)

    assert result.model_used is False
    assert result.patch["assembly"]["show_thoughts"] is False
    assert all(item.status == "skipped" for item in result.rewrites)
    assert any("未配置可用模型" in warning for warning in result.warnings)


def test_plan_survives_llm_failure(make_parsed) -> None:
    """模型侧异常不应中断适配：降级为只应用确定性修复。"""
    parsed = make_parsed()
    detection = detect(parsed)

    result = plan(parsed, detection, FakeLLM(RuntimeError("（测试语料）连接超时")))

    assert result.model_used is False
    assert result.patch["sampling"]["openai_max_tokens"] == 1024
    assert any("模型调用失败" in warning for warning in result.warnings)


def test_plan_survives_invalid_json(make_parsed) -> None:
    """模型返回自由文本时整批放弃，只保留确定性修复。"""
    parsed = make_parsed()
    detection = detect(parsed)

    result = plan(parsed, detection, FakeLLM("（测试语料）好的，我改好了！"))

    assert result.model_used is True
    assert "person-rule" not in (result.patch.get("prompts") or {})
    assert any("不是合法 JSON" in warning for warning in result.warnings)


def test_plan_accepts_fenced_json(make_parsed) -> None:
    """模型习惯用代码块包裹 JSON 时应能解析（否则实盘必挂）。"""
    parsed = make_parsed()
    detection = detect(parsed)
    fenced = "```json\n" + _items_payload(**{"person-rule": REWRITTEN_PERSON}) + "\n```"

    result = plan(parsed, detection, FakeLLM(fenced))

    assert result.patch["prompts"]["person-rule"]["content"] == REWRITTEN_PERSON


def test_plan_without_tasks_is_noop(make_parsed) -> None:
    """没有待改写条目时不调用模型。"""
    parsed = make_parsed(
        prompts=[
            {"identifier": "main", "name": "主提示", "role": "system", "content": "（测试语料）照常回应。"}
        ],
        prompt_order=[
            {"character_id": 100000, "order": [{"identifier": "main", "enabled": True}]}
        ],
        show_thoughts=False,
        openai_max_tokens=512,
        openai_max_context=8192,
    )
    detection = detect(parsed)
    llm = FakeLLM("（测试语料）不该被调用")

    result = plan(parsed, detection, llm)

    assert llm.calls == []
    assert result.model_used is False
    assert result.rewrites == []


def test_plan_result_patch_is_applicable(make_parsed) -> None:
    """产出的补丁结构必须与覆盖层语义一致（键名对不上就会静默失效）。"""
    parsed = make_parsed()
    detection = detect(parsed)

    result = plan(parsed, detection, FakeLLM(_items_payload(**{"person-rule": REWRITTEN_PERSON})))

    assert set(result.patch) <= {"assembly", "sampling", "prompts", "prompt_order"}
    assert set(result.patch["assembly"]) <= {"show_thoughts"}
    assert set(result.patch["sampling"]) <= {"openai_max_tokens", "openai_max_context"}
