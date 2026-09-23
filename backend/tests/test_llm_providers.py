"""OpenAI 兼容 provider 测试（httpx.MockTransport 模拟，无网络、无 key）。"""

import json

import httpx
import pytest

from app.llm.base import ChatMessage
from app.llm.factory import create_llm_provider
from app.llm.mock import MockLLMProvider
from app.llm.openai_compatible import DEFAULT_BASE_URLS, OpenAICompatibleProvider


def _mock_client(handler) -> httpx.Client:
    """构造注入 MockTransport 的 httpx 客户端（供 openai SDK 使用）。"""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_handler(content: str = "我在听着呢。"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1_700_000_000,
                "model": "qwen2.5-7b-instruct",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            request=request,
        )

    return handler


def test_chat_returns_content() -> None:
    provider = OpenAICompatibleProvider(
        name="dashscope",
        api_key="test-key",
        model="qwen2.5-7b-instruct",
        http_client=_mock_client(_ok_handler("听起来你最近很不容易。")),
    )
    reply = provider.chat([ChatMessage(role="user", content="我最近很累")])
    assert reply == "听起来你最近很不容易。"


def test_request_payload_shape() -> None:
    """请求体应包含 model/messages/temperature，且 role 正确。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return _ok_handler()(request)

    provider = OpenAICompatibleProvider(
        name="dashscope",
        api_key="test-key",
        model="qwen2.5-7b-instruct",
        http_client=_mock_client(handler),
    )
    provider.chat(
        [
            ChatMessage(role="system", content="你是苏澄"),
            ChatMessage(role="user", content="你好"),
        ],
        temperature=0.3,
    )
    assert captured["model"] == "qwen2.5-7b-instruct"
    assert captured["temperature"] == 0.3
    assert [m["role"] for m in captured["messages"]] == ["system", "user"]


def test_authorization_header_uses_api_key() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return _ok_handler()(request)

    provider = OpenAICompatibleProvider(
        name="siliconflow",
        api_key="sk-abc123",
        model="Qwen/Qwen2.5-7B-Instruct",
        http_client=_mock_client(handler),
    )
    provider.chat([ChatMessage(role="user", content="hi")])
    assert seen["auth"] == "Bearer sk-abc123"


def test_default_base_urls() -> None:
    assert "dashscope" in DEFAULT_BASE_URLS
    assert "siliconflow" in DEFAULT_BASE_URLS
    assert DEFAULT_BASE_URLS["dashscope"].startswith("https://")


def test_explicit_base_url_overrides_default() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return _ok_handler()(request)

    provider = OpenAICompatibleProvider(
        name="openai-compatible",
        api_key="k",
        model="custom-model",
        base_url="https://my-endpoint.example.com/v1",
        http_client=_mock_client(handler),
    )
    provider.chat([ChatMessage(role="user", content="hi")])
    assert seen["url"].startswith("https://my-endpoint.example.com/v1")


def test_empty_choices_returns_empty_string() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"id": "x", "object": "chat.completion", "created": 1, "model": "m", "choices": []},
            request=request,
        )

    provider = OpenAICompatibleProvider(
        name="dashscope", api_key="k", model="m", http_client=_mock_client(handler)
    )
    assert provider.chat([ChatMessage(role="user", content="hi")]) == ""


def test_auth_error_raises() -> None:
    """401 应抛出 SDK 鉴权异常（不静默返回空串）。"""
    import openai

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "Invalid API key", "type": "invalid_request_error"}},
            request=request,
        )

    provider = OpenAICompatibleProvider(
        name="dashscope",
        api_key="bad-key",
        model="m",
        max_retries=0,
        http_client=_mock_client(handler),
    )
    with pytest.raises(openai.AuthenticationError):
        provider.chat([ChatMessage(role="user", content="hi")])


def test_missing_api_key_raises() -> None:
    with pytest.raises(ValueError, match="api_key"):
        OpenAICompatibleProvider(name="dashscope", api_key="", model="m")


def test_missing_base_url_raises() -> None:
    """openai-compatible 无默认 base_url，必须显式提供。"""
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleProvider(name="openai-compatible", api_key="k", model="m")


# ---------- 工厂测试 ----------


def test_factory_mock_default() -> None:
    assert isinstance(create_llm_provider(""), MockLLMProvider)
    assert isinstance(create_llm_provider("mock"), MockLLMProvider)


def test_factory_dashscope() -> None:
    provider = create_llm_provider("dashscope", api_key="k", model="qwen2.5-7b-instruct")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.name == "dashscope"


def test_factory_model_default_when_empty() -> None:
    provider = create_llm_provider("siliconflow", api_key="k")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model  # 有兜底默认模型


def test_factory_unknown_raises() -> None:
    with pytest.raises(ValueError):
        create_llm_provider("not-a-provider")


# --------------------------------------------------------------------------
# 生成诊断：空回复必须能被解释
# --------------------------------------------------------------------------


def _handler_with(
    finish_reason: str, content: str = "", message_extra: dict | None = None
):
    """构造可控 finish_reason / content / 附加字段的响应。"""

    def handler(request: httpx.Request) -> httpx.Response:
        message = {"role": "assistant", "content": content}
        message.update(message_extra or {})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1_700_000_000,
                "model": "test-model",
                "choices": [
                    {"index": 0, "message": message, "finish_reason": finish_reason}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            request=request,
        )

    return handler


def _provider(handler) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        name="openai-compatible",
        api_key="test-key",
        model="test-model",
        base_url="https://example.invalid/v1",
        http_client=_mock_client(handler),
    )


def test_note_explains_empty_reply_truncated_by_length() -> None:
    """空正文 + finish_reason=length → 必须给出可读说明，不能静默返回空串。

    回归背景（实测）：推理模型把 max_tokens 全用在思考上，HTTP 200、无异常、
    正文为空——不主动解释的话，用户只会看到一片空白。
    """
    provider = _provider(_handler_with("length", ""))

    reply = provider.chat([ChatMessage(role="user", content="你好")], max_tokens=350)

    assert reply == ""
    note = provider.take_generation_note()
    assert "350" in note
    assert "length" in note


def test_note_is_cleared_after_read() -> None:
    """说明取走即清空（否则会把上一轮的问题算到下一轮头上）。"""
    provider = _provider(_handler_with("length", ""))
    provider.chat([ChatMessage(role="user", content="你好")], max_tokens=350)

    assert provider.take_generation_note()
    assert provider.take_generation_note() == ""


def test_no_note_for_normal_reply() -> None:
    """正常回复不留说明。"""
    provider = _provider(_ok_handler())

    assert provider.chat([ChatMessage(role="user", content="你好")])
    assert provider.take_generation_note() == ""


def test_empty_content_but_stopped_is_not_reported() -> None:
    """正常结束的空回复不归因于长度（避免误报）。"""
    provider = _provider(_handler_with("stop", ""))

    provider.chat([ChatMessage(role="user", content="你好")])

    assert provider.take_generation_note() == ""


def test_reasoning_content_marks_model_as_reasoning() -> None:
    """响应带 reasoning_content → 记下这是推理模型（供采样抬高额度）。"""
    provider = _provider(
        _handler_with("stop", "你好", {"reasoning_content": "（测试语料）先想一想…"})
    )

    provider.chat([ChatMessage(role="user", content="你好")])

    assert provider.saw_reasoning is True
