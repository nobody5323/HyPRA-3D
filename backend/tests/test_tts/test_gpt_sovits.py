"""GPT-SoVITS 客户端测试：请求体、时长解析、各类失败路径（全程不联网）。"""

import io
import json
import logging
import wave

import httpx
import pytest

from app.tts.gpt_sovits import (
    DEFAULT_BASE_URL,
    PROTECTED_BODY_KEYS,
    GptSovitsClient,
    GptSovitsError,
    parse_extra_params,
)

BASE_URL = "http://tts.local:9880"


def _make_wav(seconds: float = 1.0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00" * int(seconds * 16000) * 2)
    return buffer.getvalue()


def _client(handler, **overrides) -> GptSovitsClient:
    """构造一个把请求交给 handler 处理的客户端（不联网）。"""
    kwargs = {
        "base_url": BASE_URL,
        "ref_audio_path": "refs/xiaolin.wav",
        "prompt_text": "今天也辛苦了。",
        "transport": httpx.MockTransport(handler),
    }
    kwargs.update(overrides)
    return GptSovitsClient(**kwargs)


def _ok_handler(audio: bytes, captured: list[httpx.Request] | None = None):
    """返回音频的 handler；同时把请求记录下来供断言。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(200, content=audio, headers={"Content-Type": "audio/wav"})

    return handler


# ---------- 正常路径 ----------


def test_synthesize_posts_expected_payload() -> None:
    captured: list[httpx.Request] = []
    audio = _make_wav(1.0)
    client = _client(_ok_handler(audio, captured))

    result = client.synthesize_sync("我会陪着你的。")

    assert result.audio_bytes == audio
    assert result.audio_format == "wav"
    assert result.duration_s == 1.0
    assert result.has_duration is True

    request = captured[0]
    assert str(request.url) == f"{BASE_URL}/tts"      # base_url 末尾斜杠已归一化
    assert request.method == "POST"
    body = json.loads(request.content)
    assert body["text"] == "我会陪着你的。"
    assert body["text_lang"] == "zh"
    assert body["ref_audio_path"] == "refs/xiaolin.wav"
    assert body["prompt_text"] == "今天也辛苦了。"
    assert body["prompt_lang"] == "zh"
    assert body["media_type"] == "wav"
    assert body["speed_factor"] == 1.0
    assert body["streaming_mode"] is False             # 整段取回，便于按总时长对齐口型


def test_synthesize_strips_text_and_trailing_slash() -> None:
    captured: list[httpx.Request] = []
    client = _client(_ok_handler(_make_wav(), captured), base_url=BASE_URL + "/")

    client.synthesize_sync("  你好  ")

    assert str(captured[0].url) == f"{BASE_URL}/tts"
    assert json.loads(captured[0].content)["text"] == "你好"


def test_synthesize_default_base_url() -> None:
    """不传 base_url 时用官方默认端口（评审按文档启动即可对上）。"""
    client = GptSovitsClient(ref_audio_path="ref.wav")
    assert client.base_url == DEFAULT_BASE_URL
    assert client.base_url.endswith(":9880")


def test_voice_override_replaces_reference_audio() -> None:
    """按陪伴对象切音色：覆盖参考音频与参考文本，不影响客户端默认值。"""
    captured: list[httpx.Request] = []
    client = _client(_ok_handler(_make_wav(), captured))

    client.synthesize_sync(
        "换个人说话。", ref_audio_path="refs/other.wav", prompt_text="你好呀。", speed=1.2
    )

    body = json.loads(captured[0].content)
    assert body["ref_audio_path"] == "refs/other.wav"
    assert body["prompt_text"] == "你好呀。"
    assert body["speed_factor"] == 1.2
    assert client.ref_audio_path == "refs/xiaolin.wav"   # 默认值未被改写


def test_non_wav_output_has_no_duration() -> None:
    """非 WAV 拿不到时长 → duration_s 为 None，上层按估算兜底（不得报错）。"""
    client = _client(lambda request: httpx.Response(200, content=b"\x00\x01" * 500))

    result = client.synthesize_sync("你好", media_type="ogg")

    assert result.audio_format == "ogg"
    assert result.duration_s is None
    assert result.has_duration is False


# ---------- 失败路径 ----------


def test_missing_reference_audio_raises_without_request() -> None:
    """服务没部署时通常也没配参考音频：必须**在发请求前**就给出可读提示。"""
    called: list[httpx.Request] = []
    client = _client(_ok_handler(_make_wav(), called), ref_audio_path="")

    with pytest.raises(GptSovitsError, match="参考音频"):
        client.synthesize_sync("你好")

    assert called == []          # 未发请求


def test_blank_text_raises() -> None:
    client = _client(_ok_handler(_make_wav()))
    with pytest.raises(GptSovitsError, match="文本为空"):
        client.synthesize_sync("   ")


def test_invalid_media_type_raises() -> None:
    client = _client(_ok_handler(_make_wav()))
    with pytest.raises(GptSovitsError, match="media_type"):
        client.synthesize_sync("你好", media_type="flac")


def test_http_error_includes_status_and_body() -> None:
    client = _client(
        lambda request: httpx.Response(500, content="reference audio not found".encode())
    )

    with pytest.raises(GptSovitsError) as excinfo:
        client.synthesize_sync("你好")

    message = str(excinfo.value)
    assert "500" in message
    assert "reference audio not found" in message


def test_empty_audio_raises() -> None:
    client = _client(lambda request: httpx.Response(200, content=b""))

    with pytest.raises(GptSovitsError, match="空音频"):
        client.synthesize_sync("你好")


def test_connect_error_hints_service_not_started() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client(handler)

    with pytest.raises(GptSovitsError, match="无法连接 GPT-SoVITS") as excinfo:
        client.synthesize_sync("你好")

    assert BASE_URL in str(excinfo.value)


def test_timeout_raises_readable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = _client(handler, timeout=3.0)

    with pytest.raises(GptSovitsError, match="超时") as excinfo:
        client.synthesize_sync("你好")

    assert "3.0s" in str(excinfo.value)


def test_long_error_body_is_truncated() -> None:
    """服务端报错可能很长：只回显前 200 字，避免刷屏日志。"""
    client = _client(lambda request: httpx.Response(400, content=("长" * 500).encode()))

    with pytest.raises(GptSovitsError) as excinfo:
        client.synthesize_sync("你好")

    message = str(excinfo.value)
    assert message.count("长") == 200

# ---------- 额外参数（调优透传） ----------


def test_parse_extra_params_accepts_json_object() -> None:
    assert parse_extra_params('{"parallel_infer": false, "top_k": 8}') == {
        "parallel_infer": False,
        "top_k": 8,
    }


def test_parse_extra_params_blank_means_unset() -> None:
    assert parse_extra_params("") == {}
    assert parse_extra_params("   ") == {}
    assert parse_extra_params(None) == {}


def test_parse_extra_params_broken_json_is_ignored(caplog) -> None:
    """一个拼写错误不该弄挂后端起：忽略 + warning。"""
    with caplog.at_level(logging.WARNING):
        assert parse_extra_params("{parallel_infer: false}") == {}
    assert "不是合法 JSON" in caplog.records[0].getMessage()


def test_parse_extra_params_non_object_is_ignored(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_extra_params('["parallel_infer"]') == {}
    assert "应为 JSON 对象" in caplog.records[0].getMessage()


def test_extra_params_are_sent_in_request_body() -> None:
    captured: list[httpx.Request] = []
    client = _client(_ok_handler(_make_wav(), captured), extra_params={"parallel_infer": False, "top_k": 8})

    client.synthesize_sync("你好")

    body = json.loads(captured[0].content)
    assert body["parallel_infer"] is False
    assert body["top_k"] == 8
    assert body["text"] == "你好"        # 核心字段仍在


def test_extra_params_cannot_override_core_fields(caplog) -> None:
    """核心字段（说什么/什么声音/什么格式）不允许被额外参数覆盖。"""
    captured: list[httpx.Request] = []
    with caplog.at_level(logging.WARNING):
        client = _client(
            _ok_handler(_make_wav(), captured),
            extra_params={"text": "恶意覆盖", "ref_audio_path": "别的.wav", "media_type": "raw",
                          "streaming_mode": True, "speed_factor": 3.0, "top_p": 0.9},
        )
        client.synthesize_sync("真正要说的")

    body = json.loads(captured[0].content)

    assert body["text"] == "真正要说的"
    assert body["media_type"] == "wav"
    assert body["streaming_mode"] is False
    assert body["speed_factor"] == 1.0
    assert body["top_p"] == 0.9                     # 非核心字段正常透传
    assert len(caplog.records) == 5                 # 5 个被拒的键各记一条
    assert all("核心字段" in r.getMessage() for r in caplog.records)
    assert PROTECTED_BODY_KEYS >= {"text", "ref_audio_path", "media_type", "streaming_mode", "speed_factor"}
