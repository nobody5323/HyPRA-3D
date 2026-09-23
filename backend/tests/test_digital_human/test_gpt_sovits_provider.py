"""GPT-SoVITS 驱动 provider 测试：缩放对齐、音色解析、降级（全程不联网）。"""

import io
import logging
import wave

import pytest

from app.digital_human.factory import create_digital_human_provider
from app.digital_human.gpt_sovits_provider import (
    MAX_SCALE,
    MIN_SCALE,
    SOURCE_AUDIO,
    SOURCE_ESTIMATED,
    GptSovitsDigitalHumanProvider,
)
from app.digital_human.viseme import (
    build_viseme_track_estimated,
    track_duration_ms,
)
from app.tts.gpt_sovits import GptSovitsConfig, GptSovitsError, TtsAudio, VoiceRef

TEXT = "我会陪着你的，慢慢说。"


def _wav(seconds: float) -> bytes:
    """构造一段静音 WAV（时长解析需要真实容器头）。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00" * int(seconds * 16000) * 2)
    return buffer.getvalue()


def _estimated_ms(text: str = TEXT) -> int:
    return track_duration_ms(build_viseme_track_estimated(text))


class FakeClient:
    """GPT-SoVITS 客户端替身：记录调用参数，返回预设音频或抛错。"""

    def __init__(self, audio: TtsAudio | None = None, error: Exception | None = None) -> None:
        self.audio = audio
        self.error = error
        self.calls: list[dict] = []

    def synthesize_sync(self, text: str, **overrides) -> TtsAudio:
        self.calls.append({"text": text, **overrides})
        if self.error is not None:
            raise self.error
        assert self.audio is not None
        return self.audio


def _provider(client: FakeClient, tmp_path, **config_overrides) -> GptSovitsDigitalHumanProvider:
    config = GptSovitsConfig(ref_audio_path="refs/default.wav", **config_overrides)
    return GptSovitsDigitalHumanProvider(
        config=config, media_dir=tmp_path, client=client
    )


# ---------- 成功路径：音频 + 缩放对齐 ----------


def test_success_scales_visemes_to_audio_duration(tmp_path) -> None:
    client = FakeClient(TtsAudio(audio_bytes=_wav(3.0), audio_format="wav", duration_s=3.0))
    provider = _provider(client, tmp_path)

    out = provider.synthesize(TEXT, emotion="calm", intensity=0.6)

    assert out.provider == "gpt_sovits"
    assert out.has_audio is True
    assert out.audio_format == "wav"
    assert out.meta["duration_source"] == SOURCE_AUDIO
    # 缩放后总时长对齐真实音频（口型与声音同源，不再结构性错位）
    assert abs(out.duration_ms - 3000) <= 1
    assert out.meta["audio_duration_ms"] == 3000
    assert out.meta["estimated_ms"] == _estimated_ms()
    assert out.meta["align"] == "estimated-scaled"


def test_scaled_visemes_keep_char_count_and_contiguity(tmp_path) -> None:
    client = FakeClient(TtsAudio(_wav(2.0), "wav", 2.0))
    provider = _provider(client, tmp_path)

    out = provider.synthesize(TEXT)

    assert len(out.visemes) == len(TEXT)          # 缩放不改帧数
    for prev, cur in zip(out.visemes, out.visemes[1:]):
        assert prev.end_ms == cur.start_ms


def test_face_and_body_share_local_rules(tmp_path) -> None:
    """表情/动作沿用 local 的规则，保证换 TTS 后渲染体感一致。"""
    client = FakeClient(TtsAudio(_wav(2.0), "wav", 2.0))
    provider = _provider(client, tmp_path)

    out = provider.synthesize(TEXT, emotion="anxious", intensity=0.8)

    assert out.meta["expression"] == "frowning_worry"
    assert out.meta["intensity"] == 0.8
    assert out.face[-1].end_ms == out.duration_ms
    assert out.body[-1].end_ms == out.duration_ms


def test_unknown_duration_keeps_estimated_track(tmp_path) -> None:
    """非 WAV（拿不到时长）→ 保持估算轨道，并如实标注来源。"""
    client = FakeClient(TtsAudio(b"\x00\x01" * 500, "ogg", None))
    provider = _provider(client, tmp_path, media_type="ogg")

    out = provider.synthesize(TEXT)

    assert out.meta["duration_source"] == SOURCE_ESTIMATED
    assert out.meta["scale"] == 1.0
    assert out.duration_ms == _estimated_ms()
    assert out.audio_format == "ogg"
    assert out.has_audio is True                 # 音频仍可用，只是口型未校准


def test_absurd_duration_is_clamped(tmp_path) -> None:
    """时长离谱（容器头损坏/返回了别的内容）时限幅，别把口型拉成几倍速。"""
    client = FakeClient(TtsAudio(_wav(60.0), "wav", 60.0))
    provider = _provider(client, tmp_path)

    out = provider.synthesize("你好呀")

    assert out.meta["scale"] == MAX_SCALE
    assert out.duration_ms == round(_estimated_ms("你好呀") * MAX_SCALE)


def test_empty_audio_bytes_is_not_saved(tmp_path) -> None:
    """防御：客户端理论上不会返回空音频，真的返回也不能落盘一个空文件。"""
    client = FakeClient(TtsAudio(b"", "wav", None))
    provider = _provider(client, tmp_path)

    out = provider.synthesize(TEXT)

    assert out.audio_path is None
    assert out.has_audio is False
    assert list(tmp_path.glob("tts_*")) == []


# ---------- 音频落盘 ----------


def test_audio_file_named_by_content_hash(tmp_path) -> None:
    """内容寻址：同内容的重复请求复用文件，不同内容绝不互相覆盖。"""
    first = _wav(1.0)
    second = _wav(2.0)
    provider = _provider(FakeClient(TtsAudio(first, "wav", 1.0)), tmp_path)

    out_a = provider.synthesize(TEXT)
    out_b = provider.synthesize(TEXT)          # 同一份音频 → 同一个文件

    assert out_a.audio_path == out_b.audio_path
    assert len(list(tmp_path.glob("tts_*"))) == 1

    provider_other = _provider(FakeClient(TtsAudio(second, "wav", 2.0)), tmp_path)
    out_c = provider_other.synthesize(TEXT)

    assert out_c.audio_path != out_a.audio_path
    assert len(list(tmp_path.glob("tts_*"))) == 2


# ---------- 音色解析 ----------


def _voice_config(**overrides) -> GptSovitsConfig:
    kwargs = {
        "ref_audio_path": "refs/default.wav",
        "prompt_text": "默认参考文本",
        "voices": {
            "gentle": VoiceRef("refs/gentle.wav", "今天也辛苦了。", label="温柔"),
            "lively": VoiceRef("refs/lively.wav", "我们出发吧！", prompt_lang="zh"),
        },
    }
    kwargs.update(overrides)
    return GptSovitsConfig(**kwargs)


def test_voice_id_resolves_to_reference_audio(tmp_path) -> None:
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_voice_config(), media_dir=tmp_path, client=client
    )

    out = provider.synthesize(TEXT, voice="gentle")

    call = client.calls[0]
    assert call["ref_audio_path"] == "refs/gentle.wav"
    assert call["prompt_text"] == "今天也辛苦了。"
    assert call["prompt_lang"] == "zh"
    assert out.meta["voice"] == "gentle"
    assert "unknown_voice" not in out.meta


def test_default_voice_id_used_when_request_omits_voice(tmp_path) -> None:
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_voice_config(default_voice_id="lively"), media_dir=tmp_path, client=client
    )

    out = provider.synthesize(TEXT)            # 不传 voice

    assert client.calls[0]["ref_audio_path"] == "refs/lively.wav"
    assert out.meta["voice"] == "lively"


def test_unknown_voice_falls_back_to_default_and_is_reported(tmp_path) -> None:
    """未知音色 id 不该让播报失败，但必须在 meta 里留痕（否则静默失效没人知道）。"""
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_voice_config(), media_dir=tmp_path, client=client
    )

    out = provider.synthesize(TEXT, voice="not-in-table")

    assert client.calls[0]["ref_audio_path"] == "refs/default.wav"   # 回落默认音色
    assert out.meta["unknown_voice"] == "not-in-table"
    assert out.provider == "gpt_sovits"                             # 仍正常出声


def test_no_voice_and_no_default_uses_config_reference(tmp_path) -> None:
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = _provider(client, tmp_path)

    out = provider.synthesize(TEXT)

    assert client.calls[0]["ref_audio_path"] == "refs/default.wav"
    assert client.calls[0]["prompt_text"] == ""
    assert out.meta["voice"] == ""


# ---------- 降级路径 ----------


def test_degrades_to_local_on_tts_failure(tmp_path) -> None:
    client = FakeClient(error=GptSovitsError("无法连接 GPT-SoVITS（http://127.0.0.1:9880）"))
    provider = _provider(client, tmp_path)

    out = provider.synthesize(TEXT, emotion="anxious", intensity=0.7)

    assert out.provider == "local"                       # 已降级
    assert out.has_audio is False
    assert out.meta["degraded_from"] == "gpt_sovits"
    assert "无法连接" in out.meta["degrade_reason"]
    assert out.visemes and out.duration_ms > 0           # 仍产出可用时间轴


def test_missing_reference_audio_degrades_without_network(tmp_path) -> None:
    """服务没部署（也没配参考音频）时：构造 provider 不报错，调用降级且不发请求。"""
    provider = GptSovitsDigitalHumanProvider(
        config=GptSovitsConfig(), media_dir=tmp_path        # base_url 默认指向本机 9880
    )

    out = provider.synthesize(TEXT)

    assert out.provider == "local"
    assert "参考音频" in out.meta["degrade_reason"]


# ---------- 工厂 ----------


def test_factory_creates_gpt_sovits_provider() -> None:
    provider = create_digital_human_provider("gpt_sovits", media_dir="media")
    assert isinstance(provider, GptSovitsDigitalHumanProvider)
    assert provider.name == "gpt_sovits"


@pytest.mark.parametrize("alias", ["gpt-sovits", "GPT_SOVITS", "gptsovits"])
def test_factory_accepts_provider_aliases(alias: str) -> None:
    assert isinstance(
        create_digital_human_provider(alias), GptSovitsDigitalHumanProvider
    )


def test_factory_unknown_provider_message_lists_options() -> None:
    with pytest.raises(ValueError, match="gpt_sovits"):
        create_digital_human_provider("nope")


def test_scale_bounds_are_sane() -> None:
    """限幅区间本身要合理：下界不能被 0 除，上界不能大到失去意义。"""
    assert 0 < MIN_SCALE < 1 < MAX_SCALE <= 5

# ---------- 情绪 → 音色（音色表 _emotion_map） ----------


def _emotion_config(**overrides) -> GptSovitsConfig:
    """带情绪映射的音色表：sad 走 gentle、happy 走 lively。"""
    kwargs = {
        "ref_audio_path": "refs/default.wav",
        "prompt_text": "默认参考文本",
        "default_voice_id": "gentle",
        "voices": {
            "gentle": VoiceRef("refs/gentle.wav", "嗯。", label="温柔"),
            "lively": VoiceRef("refs/lively.wav", "走啦！", label="活泼"),
        },
        "emotion_voices": {"sad": "lively", "tired": "lively"},
    }
    kwargs.update(overrides)
    return GptSovitsConfig(**kwargs)


def test_emotion_picks_voice_when_no_voice_requested(tmp_path) -> None:
    """不传 voice 时按本轮情绪选音色（情绪联动的声音）——这是这套映射的核心价值。"""
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_emotion_config(), media_dir=tmp_path, client=client
    )

    out = provider.synthesize(TEXT, emotion="sad")

    assert client.calls[0]["ref_audio_path"] == "refs/lively.wav"
    assert out.meta["voice"] == "lively"
    assert out.meta["voice_source"] == "emotion"
    assert "unknown_voice" not in out.meta


def test_emotion_map_miss_falls_back_to_default_voice(tmp_path) -> None:
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_emotion_config(), media_dir=tmp_path, client=client
    )

    out = provider.synthesize(TEXT, emotion="happy")     # 未映射

    assert client.calls[0]["ref_audio_path"] == "refs/gentle.wav"
    assert out.meta["voice"] == "gentle"
    assert out.meta["voice_source"] == "default"


def test_explicit_voice_beats_emotion(tmp_path) -> None:
    """显式指定优先：用户在设置里手选了音色，就不该被情绪改掉。"""
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_emotion_config(), media_dir=tmp_path, client=client
    )

    out = provider.synthesize(TEXT, emotion="sad", voice="gentle")

    assert client.calls[0]["ref_audio_path"] == "refs/gentle.wav"
    assert out.meta["voice"] == "gentle"
    assert out.meta["voice_source"] == "request"


def test_unknown_voice_does_not_fall_through_to_emotion(tmp_path) -> None:
    """显式传了未知音色 → 回落默认，**不**按情绪换：拼错必须是可预测的行为。"""
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_emotion_config(), media_dir=tmp_path, client=client
    )

    out = provider.synthesize(TEXT, emotion="sad", voice="typo-voice")

    assert client.calls[0]["ref_audio_path"] == "refs/gentle.wav"   # default，而非 lively
    assert out.meta["unknown_voice"] == "typo-voice"
    assert out.meta["voice_source"] == "default"


def test_emotion_matching_is_case_insensitive(tmp_path) -> None:
    """后端情绪标签统一小写，配置里写成大写也要能用。"""
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_emotion_config(emotion_voices={"SAD": "lively"}), media_dir=tmp_path, client=client
    )

    out = provider.synthesize(TEXT, emotion="sad")

    assert out.meta["voice"] == "lively"


def test_no_voice_table_at_all_uses_config_reference(tmp_path) -> None:
    """连音色表都没有时：voice_source=config（排查时一眼看出"没走音色表"）。"""
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=GptSovitsConfig(ref_audio_path="refs/only.wav", prompt_text="只有默认"),
        media_dir=tmp_path, client=client,
    )

    out = provider.synthesize(TEXT, emotion="sad")

    assert client.calls[0]["ref_audio_path"] == "refs/only.wav"
    assert out.meta["voice_source"] == "config"
    assert out.meta["voice"] == ""

# ---------- 预热 ----------


def test_warmup_synthesizes_once_without_saving(tmp_path) -> None:
    """预热要真的调一次合成（否则引擎没热），但**不落盘**（它只是预热，不是播报）。"""
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = _provider(client, tmp_path)

    assert provider.warmup() is True

    assert len(client.calls) == 1
    assert client.calls[0]["text"] == "预热。"
    assert list(tmp_path.glob("tts_*")) == []


def test_warmup_uses_resolved_default_voice(tmp_path) -> None:
    """预热也要走音色解析：用默认音色的参考音频，否则热的是别人的音色。"""
    client = FakeClient(TtsAudio(_wav(1.0), "wav", 1.0))
    provider = GptSovitsDigitalHumanProvider(
        config=_voice_config(default_voice_id="gentle"), media_dir=tmp_path, client=client
    )

    provider.warmup()

    assert client.calls[0]["ref_audio_path"] == "refs/gentle.wav"


def test_warmup_failure_is_swallowed(tmp_path, caplog) -> None:
    """服务没起来时预热失败：只记日志，绝不抛（预热是优化，不是启动前置条件）。"""
    client = FakeClient(error=GptSovitsError("无法连接 GPT-SoVITS"))
    provider = _provider(client, tmp_path)

    with caplog.at_level(logging.WARNING):
        assert provider.warmup() is False
    assert "预热失败" in caplog.records[0].getMessage()
