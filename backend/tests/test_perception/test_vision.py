"""视觉理解（Qwen2.5-VL）测试：无网络、无 key（httpx.MockTransport 模拟）。"""

import base64
import json

import httpx
import pytest

from app.perception.vision import (
    VISION_PROVIDERS,
    VisionError,
    create_vision_provider,
    create_vision_provider_from_settings,
)
from app.perception.vision.none_provider import NoneVisionProvider
from app.perception.vision.openai_compatible import (
    DEFAULT_BASE_URLS,
    DEFAULT_VISION_PROMPT,
    OpenAiCompatibleVisionProvider,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _capture_handler(content: str = "窗边坐着一只橘猫，窗外在下雨。"):
    """记录请求体的 handler（用于断言请求真的带了图片）。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-vl",
                "object": "chat.completion",
                "created": 1_700_000_000,
                "model": "qwen2.5-vl-72b-instruct",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            },
            request=request,
        )

    return handler, captured


def _provider(handler, **overrides) -> OpenAiCompatibleVisionProvider:
    kwargs = dict(
        api_key="test-key",
        model="qwen2.5-vl-72b-instruct",
        provider_name="dashscope",
        http_client=_mock_client(handler),
    )
    kwargs.update(overrides)
    return OpenAiCompatibleVisionProvider(**kwargs)


# ---------- none / 工厂 ----------


def test_none_provider_is_unavailable_and_refuses() -> None:
    provider = NoneVisionProvider()
    assert provider.available() is False
    with pytest.raises(VisionError, match="未启用"):
        provider.describe(PNG)


@pytest.mark.parametrize("name", ["", "none", "写错了"])
def test_unknown_provider_falls_back_to_none(name: str) -> None:
    """视觉是可选能力：配置写错只该让「发图」入口消失，不该让后端起不来。"""
    assert create_vision_provider(name).name == "none"


@pytest.mark.parametrize("name", ["openai-compatible", "qwen-vl", "qwen2.5-vl", "dashscope"])
def test_aliases_resolve_to_vl_provider(name: str) -> None:
    provider = create_vision_provider(name, api_key="k", model="m")
    assert isinstance(provider, OpenAiCompatibleVisionProvider)


def test_provider_from_settings_reads_all_fields() -> None:
    from app.config import Settings

    settings = Settings(
        vision_provider="siliconflow",
        vision_api_key="k",
        vision_model="Qwen/Qwen2.5-VL-72B-Instruct",
        vision_base_url="",
        vision_timeout=30.0,
    )
    provider = create_vision_provider_from_settings(settings)
    assert provider.available() is True
    assert provider.model == "Qwen/Qwen2.5-VL-72B-Instruct"
    # 别名值同时充当「用哪家的默认端点」——用户只填一个字段就能跑通
    assert provider.base_url == DEFAULT_BASE_URLS["siliconflow"]


# ---------- 可用性 ----------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, False),
        # model 有内置默认值，因此「缺 model」要显式传空串才是真的缺
        ({"api_key": "k", "model": ""}, False),
        ({"model": "m"}, False),  # 缺 key
        ({"api_key": "k", "model": "m"}, True),
    ],
)
def test_available_requires_key_and_model(kwargs, expected) -> None:
    """`available()` **不发起请求**——状态查询是高频接口。"""
    assert OpenAiCompatibleVisionProvider(**kwargs).available() is expected


def test_missing_key_raises_actionable_error() -> None:
    provider = OpenAiCompatibleVisionProvider(model="m", provider_name="dashscope")
    with pytest.raises(VisionError, match="VISION_API_KEY"):
        provider.describe(PNG)


def test_missing_base_url_raises_actionable_error() -> None:
    provider = OpenAiCompatibleVisionProvider(
        api_key="k", model="m", provider_name="unknown-vendor"
    )
    with pytest.raises(VisionError, match="VISION_BASE_URL"):
        provider.describe(PNG)


def test_empty_image_rejected() -> None:
    handler, _ = _capture_handler()
    with pytest.raises(VisionError, match="图片内容为空"):
        _provider(handler).describe(b"")


# ---------- 请求构造 ----------


def test_image_is_sent_inline_as_data_url() -> None:
    """图片以 data URL 内联（**不落盘**）——§4.6 红线。"""
    handler, captured = _capture_handler()
    result = _provider(handler).describe(PNG, mime="image/png")

    content = captured["body"]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG
    assert result.description == "窗边坐着一只橘猫，窗外在下雨。"


def test_default_prompt_forbids_inference() -> None:
    """推断是人设的职责，感知层只提供素材。"""
    handler, captured = _capture_handler()
    _provider(handler).describe(PNG)

    text = captured["body"]["messages"][0]["content"][0]["text"]
    assert text == DEFAULT_VISION_PROMPT
    assert "不要推断" in text
    assert "不要写任何建议" in text


def test_question_narrows_focus_but_keeps_neutrality() -> None:
    """带问题时也不能变成「回答问题」——那会把对话内容混进感知事实。"""
    handler, captured = _capture_handler()
    _provider(handler).describe(PNG, question="它看起来开心吗")

    text = captured["body"]["messages"][0]["content"][0]["text"]
    assert "它看起来开心吗" in text
    assert "不要直接回答或给建议" in text
    # 客观描述纪律仍在
    assert "不要推断" in text


def test_blank_question_uses_default_prompt() -> None:
    handler, captured = _capture_handler()
    _provider(handler).describe(PNG, question="   ")
    text = captured["body"]["messages"][0]["content"][0]["text"]
    assert text == DEFAULT_VISION_PROMPT


def test_custom_prompt_overrides_default() -> None:
    handler, captured = _capture_handler()
    _provider(handler, prompt="只说画面里有几个人。").describe(PNG)
    text = captured["body"]["messages"][0]["content"][0]["text"]
    assert text == "只说画面里有几个人。"


# ---------- 错误与空结果 ----------


def test_api_error_becomes_actionable_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid API key"}}, request=request)

    with pytest.raises(VisionError) as excinfo:
        _provider(handler, max_retries=0).describe(PNG)
    message = str(excinfo.value)
    assert "图片理解失败" in message
    assert "qwen2.5-vl-72b-instruct" in message  # 带上模型名，便于排查
    assert "API Key" in message


def test_empty_model_output_is_reported_not_raised() -> None:
    """「描述为空」不是错误——只是模型没看出什么（与配置问题区分开）。"""
    handler, _ = _capture_handler(content="")
    result = _provider(handler).describe(PNG)
    assert result.empty is True
    assert result.warnings


def test_provider_registry_lists_none_and_aliases() -> None:
    assert "none" in VISION_PROVIDERS
    assert "openai-compatible" in VISION_PROVIDERS
