"""LLM 辅助创作测试（`AGENTS.md §9.12`）。

两类内容（人设 / 技能）共用同一套生成底座，这里覆盖底座里最容易出错的四件事：

1. **mock 一律拒绝**——它是固定占位回复，生成出来的东西必然不可用；
2. **容错解析**——模型爱写 ```json 围栏、爱在 JSON 前后加解释，这不该算失败；
3. **字段清洗**——未知状态变量要剔除，id 要规范化，过短/缺字段要明确报错；
4. **失败可重试**——第一次解析失败会追加纠正再试一次，仍不行才报错。

插件**不在**这套底座里：插件是会被宿主 import 并执行的代码，由用户自己写
（见 `docs/plugin-development.md`），宿主不生成插件代码。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.llm.authoring import (
    AuthoringFailed,
    AuthoringUnavailable,
    PersonaDraft,
    SkillDraft,
    extract_json,
    generate_draft,
    load_prompt,
    normalize_persona_draft,
    normalize_skill_draft,
    resolve_authoring_provider,
    set_provider_resolver,
    slugify_id,
    validate_id,
)
from app.llm.base import ChatMessage, LLMProvider


class ScriptedProvider(LLMProvider):
    """按脚本逐次返回固定文本的 provider（替代真实模型）。"""

    name = "scripted"
    model = "scripted-model"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    def chat(self, messages: list[ChatMessage], **_: Any) -> str:  # type: ignore[override]
        self.calls.append(list(messages))
        return self._replies.pop(0) if self._replies else ""


@pytest.fixture(autouse=True)
def _reset_resolver():
    """每个用例后恢复默认解析器（否则注入会漏到别的测试）。"""
    yield
    set_provider_resolver(None)


# =============================================================
# 模型解析：mock 必须被拒
# =============================================================


def test_mock_provider_is_rejected() -> None:
    """本地占位实现（mock）直接拒绝：它的回复是写死的文本。"""
    from app.llm.mock import MockLLMProvider

    set_provider_resolver(lambda: MockLLMProvider())

    with pytest.raises(AuthoringUnavailable) as excinfo:
        resolve_authoring_provider()

    assert "占位模型" in str(excinfo.value)


def test_missing_provider_is_rejected() -> None:
    set_provider_resolver(lambda: None)

    with pytest.raises(AuthoringUnavailable):
        resolve_authoring_provider()


def test_resolver_exception_becomes_readable_error() -> None:
    def boom() -> None:
        raise RuntimeError("缺 API Key")

    set_provider_resolver(boom)

    with pytest.raises(AuthoringUnavailable) as excinfo:
        resolve_authoring_provider()

    assert "缺 API Key" in str(excinfo.value)


def test_scripted_provider_passes() -> None:
    provider = ScriptedProvider(["{}"])
    set_provider_resolver(lambda: provider)

    assert resolve_authoring_provider() is provider


# =============================================================
# 容错 JSON 解析
# =============================================================


def test_extract_json_handles_fences_and_chatter() -> None:
    """三种真实会遇到的形态：裸 JSON、带围栏、JSON 前后有说明文字。"""
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('好的，这是结果：\n{"a": 1}\n希望有帮助') == {"a": 1}


def test_extract_json_handles_nested_and_braces_in_strings() -> None:
    """括号配平不能数错：字符串里的 `{`、转义引号都算字符，不算结构。"""
    payload = '说明\n{"a": {"b": "带 { 括号 } 与 \\" 引号"}, "c": 2}\n结束'

    assert extract_json(payload) == {"a": {"b": '带 { 括号 } 与 " 引号'}, "c": 2}


def test_extract_json_rejects_empty_and_unclosed() -> None:
    for bad in ("", "   ", "没有 JSON", '{"a": 1'):
        with pytest.raises(ValueError):
            extract_json(bad)


# =============================================================
# 生成：重试与报错
# =============================================================


def test_generate_draft_retries_once_after_parse_failure() -> None:
    """第一次乱写 → 追加纠正再问一次；第二次正常则成功。"""
    provider = ScriptedProvider(
        [
            "这不是 JSON",
            '{"name": "苏澄", "prompt": "' + "她说话很轻。" * 20 + '", "variables": ["user_name"]}',
        ]
    )
    set_provider_resolver(lambda: provider)

    outcome = generate_draft(task="persona", brief="想做个话少的姐姐", model_cls=PersonaDraft)

    assert outcome.draft.name == "苏澄"
    assert outcome.model == "scripted-model"
    # 第二次请求里必须带上「只输出 JSON」的纠正
    assert len(provider.calls) == 2
    assert "只" in provider.calls[1][-1].content


def test_generate_draft_retry_covers_content_problems() -> None:
    """清洗器报错（如技能正文过短）也能触发重试，并把具体错因回给模型。"""
    bad = '{"id": "x", "name": "降火", "body": "太短"}'
    good = '{"id": "x", "name": "降火", "body": "## 步骤\\n' + "先复述。" * 30 + '"}'
    provider = ScriptedProvider([bad, good])
    set_provider_resolver(lambda: provider)

    outcome = generate_draft(
        task="skill", brief="随便", model_cls=SkillDraft, normalize=normalize_skill_draft
    )

    assert outcome.draft.id == "x"
    assert len(provider.calls) == 2
    assert "过短" in provider.calls[1][-1].content


def test_generate_draft_fails_after_two_bad_replies() -> None:
    provider = ScriptedProvider(["nope", "still nope"])
    set_provider_resolver(lambda: provider)

    with pytest.raises(AuthoringFailed) as excinfo:
        generate_draft(task="skill", brief="随便", model_cls=SkillDraft)

    assert "原始回复开头" in str(excinfo.value)


class ExplodingProvider(LLMProvider):
    """调用必失败的 provider（模拟 key 过期 / 断网 / 限流）。"""

    name = "openai-compatible"
    model = "qwen-plus"

    def __init__(self, message: str = "401 Unauthorized", api_key: str = "") -> None:
        self.message = message
        self.api_key = api_key

    def chat(self, messages: list[ChatMessage], **_: Any) -> str:
        raise RuntimeError(self.message)


def test_model_call_failure_becomes_readable_error() -> None:
    """模型调用失败（最常见的失败模式）不能变成裸 500：要带原因地报回去。"""
    provider = ExplodingProvider("401 Unauthorized")
    set_provider_resolver(lambda: provider)

    with pytest.raises(AuthoringFailed) as excinfo:
        generate_draft(task="skill", brief="随便写一个", model_cls=SkillDraft)

    assert "模型调用失败" in str(excinfo.value)
    assert "401" in str(excinfo.value)


def test_model_call_failure_redacts_api_key() -> None:
    """异常里可能混着密钥（请求头 / URL）——脱敏后再回传。"""
    provider = ExplodingProvider("invalid key sk-secret-123", api_key="sk-secret-123")
    set_provider_resolver(lambda: provider)

    with pytest.raises(AuthoringFailed) as excinfo:
        generate_draft(task="skill", brief="随便写一个", model_cls=SkillDraft)

    assert "sk-secret-123" not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_generate_draft_requires_brief() -> None:
    provider = ScriptedProvider(['{"id": "x"}'])
    set_provider_resolver(lambda: provider)

    with pytest.raises(AuthoringFailed):
        generate_draft(task="skill", brief="   ", model_cls=SkillDraft)


def test_prompts_exist_and_mention_json() -> None:
    """两份提示词都要能读到（打包漏拷会在这里炸），且都明确要求 JSON 输出。"""
    for task in ("persona", "skill"):
        prompt = load_prompt(task)
        assert "JSON" in prompt
        assert len(prompt) > 200


# =============================================================
# 草稿清洗
# =============================================================


def test_persona_draft_drops_unknown_state_vars() -> None:
    draft = PersonaDraft(
        name=" 苏澄 ",
        title="会听人说话的姐姐",
        prompt="你是苏澄。" * 20,
        tags=["温柔", "", "  ", "可靠"],
        variables=["user_name", "not_a_var", "char_name"],
    )

    cleaned = normalize_persona_draft(draft)

    assert cleaned.name == "苏澄"
    assert cleaned.variables == ["user_name", "char_name"]
    assert cleaned.tags == ["温柔", "可靠"]


def test_persona_draft_rejects_too_short_prompt() -> None:
    """过短的正文是模型偷懒——报错让用户重试，而不是默默存下一段废话。"""
    with pytest.raises(AuthoringFailed):
        normalize_persona_draft(PersonaDraft(name="苏澄", prompt="你是苏澄。"))


def test_skill_draft_normalizes_id_and_requires_body() -> None:
    cleaned = normalize_skill_draft(
        SkillDraft(id="De-Escalate Anger!", name="降火", body="## 步骤\n" + "先复述。" * 30)
    )
    assert cleaned.id == "de-escalate-anger"

    with pytest.raises(AuthoringFailed):
        normalize_skill_draft(SkillDraft(id="x", name="短", body="太短"))


def test_slugify_and_validate_id() -> None:
    assert slugify_id("喝水 记录!!") == "generated"  # 全中文 → 无可用字符，回落
    assert slugify_id("Water-Tracker 2") == "water-tracker-2"
    assert validate_id("good-id", label="技能 id") == "good-id"

    for bad in ("Bad ID", "", "-lead", "a" * 80):
        with pytest.raises(ValueError):
            validate_id(bad, label="插件 id")
