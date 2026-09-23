"""数字人驱动测试：口型时间轴、本地实现、鉴权签名、魔珐降级、API。"""

import hashlib
import json

import pytest

from app.digital_human.local_provider import LocalDigitalHumanProvider
from app.digital_human.models import AvatarOutput, Viseme
from app.digital_human.viseme import (
    build_viseme_track_estimated,
    build_viseme_track_from_char_times,
    classify_char,
    scale_track_ms,
    track_duration_ms,
)
from app.digital_human.xmov_auth import build_signature, encode_with_md5
from app.digital_human.xmov_provider import XmovDigitalHumanProvider
from app.digital_human.xmov_tts import _parse_char_time_map


# ---------- 口型时间轴 ----------


def test_classify_punctuation_is_silence() -> None:
    for ch in "，。！？；：、 \n":
        assert classify_char(ch) == Viseme.SIL


def test_classify_deterministic() -> None:
    """同一字必得同一口型（可测试、可复现）。"""
    assert classify_char("小") == classify_char("小")
    assert classify_char("林") == classify_char("林")


def test_classify_avoids_repeating_previous() -> None:
    prev = classify_char("小")
    assert classify_char("小", 1, prev) != prev


def test_estimated_track_covers_text() -> None:
    text = "我会陪着你的，慢慢说。"
    frames = build_viseme_track_estimated(text)
    assert len(frames) == len(text)
    assert frames[0].start_ms == 0
    # 时间轴单调递增且无重叠
    for prev, cur in zip(frames, frames[1:]):
        assert prev.end_ms == cur.start_ms


def test_estimated_track_pause_longer_than_char() -> None:
    frames = build_viseme_track_estimated("好，")
    assert frames[1].viseme == Viseme.SIL
    assert frames[1].duration_ms > frames[0].duration_ms


def test_scale_track_ms_hits_target_duration() -> None:
    frames = build_viseme_track_estimated("我会在这里陪着你，慢慢说。")
    scaled = scale_track_ms(frames, 5000)
    # 每帧边界各自取整，允许 1ms 的累计误差
    assert abs(track_duration_ms(scaled) - 5000) <= 1


def test_scale_track_ms_keeps_frames_contiguous() -> None:
    """缩放后仍须「首尾相接、单调不重叠」——否则口型会跳变。"""
    frames = build_viseme_track_estimated("我会在这里陪着你。")
    for target_ms in (600, 3000):          # 压缩与拉伸各测一次
        scaled = scale_track_ms(frames, target_ms)
        assert len(scaled) == len(frames)
        for prev, cur in zip(scaled, scaled[1:]):
            assert prev.end_ms == cur.start_ms
            assert prev.end_ms >= prev.start_ms


def test_scale_track_ms_preserves_viseme_and_char() -> None:
    """缩放只改时间，不改口型与文字。"""
    frames = build_viseme_track_estimated("好听，")
    scaled = scale_track_ms(frames, 900)
    assert [f.viseme for f in scaled] == [f.viseme for f in frames]
    assert [f.char for f in scaled] == [f.char for f in frames]


def test_scale_track_ms_returns_copy_for_invalid_target() -> None:
    """目标时长非法（0 / 负数）时原样返回，且不共享列表对象。"""
    frames = build_viseme_track_estimated("你好")
    for target_ms in (0, -100):
        out = scale_track_ms(frames, target_ms)
        assert out == frames
        assert out is not frames


def test_scale_track_ms_handles_empty_track() -> None:
    assert scale_track_ms([], 1000) == []


def test_char_time_track_uses_exact_timestamps() -> None:
    """字级时间戳应精确反映到口型帧。"""
    char_times = [("这", 0.0, 0.1349), ("是", 0.1349, 0.2383)]
    frames = build_viseme_track_from_char_times(char_times)
    assert frames[0].start_ms == 0
    assert frames[0].end_ms == 135
    assert frames[1].start_ms == 135
    assert frames[1].end_ms == 238
    assert track_duration_ms(frames) == 238


def test_char_time_track_marks_punctuation() -> None:
    frames = build_viseme_track_from_char_times([("，", 1.0, 1.5)])
    assert frames[0].viseme == Viseme.SIL


def test_parse_char_time_map_punc_token() -> None:
    payload = json.dumps([["这", 0.0, 0.1], ["[PUNC]", 0.1, 0.5]])
    parsed = _parse_char_time_map(payload)
    assert parsed[0][0] == "这"
    assert parsed[1][0] == "，"     # [PUNC] → 停顿标点
    assert parsed[1][2] == 0.5


# ---------- 本地 provider ----------


def test_local_provider_outputs_timelines() -> None:
    provider = LocalDigitalHumanProvider()
    out = provider.synthesize("我会在这里陪你。", emotion="anxious", intensity=0.8)

    assert isinstance(out, AvatarOutput)
    assert out.provider == "local"
    assert out.duration_ms > 0
    assert out.visemes and out.face and out.body
    assert out.has_audio is False           # 本地无音频
    assert out.meta["expression"] == "frowning_worry"   # anxious 映射
    assert out.meta["intensity"] == 0.8


def test_local_provider_face_track_has_fade() -> None:
    out = LocalDigitalHumanProvider().synthesize("测试文本内容", emotion="happy")
    assert len(out.face) == 3
    assert out.face[0].intensity < out.face[1].intensity  # 淡入
    assert out.face[2].end_ms == out.duration_ms


def test_local_provider_body_amplitude_follows_intensity() -> None:
    low = LocalDigitalHumanProvider().synthesize("你好", intensity=0.1)
    high = LocalDigitalHumanProvider().synthesize("你好", intensity=0.9)
    assert high.body[0].intensity > low.body[0].intensity


def test_local_provider_unknown_emotion_defaults() -> None:
    out = LocalDigitalHumanProvider().synthesize("文本", emotion="unknown-emotion")
    assert out.meta["expression"] == "default"


def test_to_dict_serializable() -> None:
    out = LocalDigitalHumanProvider().synthesize("你好", emotion="calm")
    payload = out.to_dict()
    json.dumps(payload)          # 可 JSON 序列化
    assert payload["visemes"][0]["viseme"]
    assert set(payload) >= {"text", "duration_ms", "visemes", "face", "body"}


# ---------- 鉴权签名 ----------


def test_signature_deterministic_with_fixed_timestamp() -> None:
    """固定时间戳时签名可复现（便于测试）。"""
    headers = build_signature("ak-1", "secret-1", "POST", "/user/v1/tts_task/create_tts_task",
                              {"text": "你好", "tts_vcn": "V1"}, timestamp=1700000000)
    assert headers["X-APP-ID"] == "ak-1"
    assert headers["X-TIMESTAMP"] == "1700000000"
    # 手工复算校验
    expected_src = (
        "/user/v1/tts_task/create_tts_task"
        + "post"
        + json.dumps({"text": "你好", "tts_vcn": "V1"}, sort_keys=True).replace(" ", "")
        + "secret-1"
        + "1700000000"
    )
    assert headers["X-TOKEN"] == hashlib.md5(expected_src.encode("utf-8")).hexdigest()


def test_signature_sorts_keys() -> None:
    """参数顺序不同不应影响签名（内部排序）。"""
    a = build_signature("ak", "s", "POST", "/p", {"b": 1, "a": 2}, timestamp=1)
    b = build_signature("ak", "s", "POST", "/p", {"a": 2, "b": 1}, timestamp=1)
    assert a["X-TOKEN"] == b["X-TOKEN"]


def test_md5_helper() -> None:
    assert encode_with_md5("abc") == hashlib.md5(b"abc").hexdigest()


# ---------- 魔珐 provider ----------


def test_xmov_requires_credentials() -> None:
    with pytest.raises(ValueError, match="XMOV_APP_ID"):
        XmovDigitalHumanProvider(app_id="", secret="")


def test_xmov_degrades_to_local_on_failure(monkeypatch, tmp_path) -> None:
    """TTS 调用失败时应降级到本地实现，并在 meta 标注原因。"""
    provider = XmovDigitalHumanProvider(
        app_id="ak", secret="secret", media_dir=tmp_path
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("模拟 TTS 不可用")

    monkeypatch.setattr("app.digital_human.xmov_provider.synthesize_sync", _boom)
    out = provider.synthesize("我会陪着你。", emotion="anxious", intensity=0.7)

    assert out.provider == "local"                     # 已降级
    assert out.meta["degraded_from"] == "xmov"
    assert "模拟 TTS 不可用" in out.meta["degrade_reason"]
    assert out.visemes and out.duration_ms > 0         # 仍产出可用时间轴


def test_xmov_uses_char_times_when_available(monkeypatch, tmp_path) -> None:
    """TTS 成功时：口型时间轴来自字级时间戳（精确对齐）。"""
    from app.digital_human.xmov_tts import TtsResult

    provider = XmovDigitalHumanProvider(app_id="ak", secret="s", media_dir=tmp_path)

    def _fake_sync(text, **kwargs):
        return TtsResult(
            audio_bytes=b"\x00\x01" * 10,
            char_times=[("我", 0.0, 0.2), ("在", 0.2, 0.45)],
            voice="V1",
            duration_s=0.45,
            raw_messages=3,
        )

    monkeypatch.setattr("app.digital_human.xmov_provider.synthesize_sync", _fake_sync)
    out = provider.synthesize("我在", emotion="calm", intensity=0.5)

    assert out.provider == "xmov"
    assert out.duration_ms == 450
    assert out.visemes[0].end_ms == 200          # 来自字级时间戳
    assert out.has_audio is True
    assert out.audio_path and out.audio_path.endswith(".pcm")
    assert (tmp_path).glob("tts_*.pcm")          # 音频已落盘


# ---------- API ----------


def test_avatar_endpoint_local() -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.post(
        "/media/avatar",
        json={"text": "我在这里陪你，慢慢说。", "emotion": "anxious", "intensity": 0.8},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "local"
    assert body["duration_ms"] > 0
    assert body["visemes"] and body["face"] and body["body"]
    assert body["meta"]["expression"] == "frowning_worry"


def test_avatar_endpoint_requires_text() -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    assert client.post("/media/avatar", json={"text": ""}).status_code == 422


def test_avatar_audio_route_rejects_unsafe_path() -> None:
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    # 目录穿越
    assert client.get("/media/audio/..%2F.env").status_code in (400, 404)
    # 非法后缀
    assert client.get("/media/audio/secret.txt").status_code == 400
    # 不存在
    assert client.get("/media/audio/missing.pcm").status_code == 404
