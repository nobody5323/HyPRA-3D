"""语音引擎配置接线与 /media/tts/voices、/media/avatar 音频取回测试。

覆盖三类真实故障：
- 设置面板在页面加载时就请求 /media/tts/voices：**该接口不能 500**
  （例如配了 xmov 却没填密钥）；
- 音色表/参考音频没配时，前端必须能看出"该回落浏览器 TTS"；
- 后端产出的音频地址必须**真能取回**（曾经漏配 .aac 白名单就会 400）。
"""

import io
import json
import wave

import pytest
from fastapi.testclient import TestClient

from app.api import media as media_module
from app.config import get_settings
from app.digital_human.gpt_sovits_provider import GptSovitsDigitalHumanProvider
from app.digital_human.local_provider import LocalDigitalHumanProvider
from app.main import app
from app.tts.gpt_sovits import GptSovitsConfig, TtsAudio, VoiceRef

client = TestClient(app)


def _wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00" * int(seconds * 16000) * 2)
    return buffer.getvalue()


def _voice_ref(ref: str, prompt: str, label: str) -> VoiceRef:
    """构造一个音色（测试内用，语义比直接写 VoiceRef 清楚）。"""
    return VoiceRef(ref_audio_path=ref, prompt_text=prompt, label=label)


@pytest.fixture(autouse=True)
def isolated_media_dir(tmp_path, monkeypatch):
    """音频目录指向临时目录：绝不往真实的 backend/media 里写测试文件。"""
    media_dir = tmp_path / "media"
    monkeypatch.setenv("MEDIA_DIR", str(media_dir))
    return media_dir


class FakeClient:
    """GPT-SoVITS 客户端替身（不出网）。"""

    def __init__(self, audio: TtsAudio) -> None:
        self.audio = audio

    def synthesize_sync(self, text: str, **overrides) -> TtsAudio:
        return self.audio


def _use_gpt_sovits(tmp_path, *, voices=None, ref_audio="", default_voice_id="") -> None:
    """把 gpt_sovits provider 注入 media 模块（模拟 .env 配好的状态）。"""
    provider = GptSovitsDigitalHumanProvider(
        config=GptSovitsConfig(
            ref_audio_path=ref_audio,
            prompt_text="参考文本",
            default_voice_id=default_voice_id,
            voices=voices or {},
        ),
        media_dir=tmp_path / "media",
        client=FakeClient(TtsAudio(_wav(2.0), "wav", 2.0)),
    )
    media_module.set_digital_human_provider(provider)


# ---------- GET /media/tts/voices ----------


def test_voices_endpoint_local_provider_reports_browser_tts() -> None:
    """默认（local 驱动）→ 前端应知道"没有服务端音频，用浏览器 TTS"。"""
    resp = client.get("/media/tts/voices")

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "local"
    assert body["server_tts"] is False
    assert body["voices"] == []
    assert body["note"]          # 有可展示的说明，前端不必自己拼文案


def test_voices_endpoint_lists_table_and_marks_default(tmp_path) -> None:
    _use_gpt_sovits(
        tmp_path,
        voices={
            "gentle": _voice_ref("refs/gentle.wav", "今天也辛苦了。", "温柔"),
            "lively": _voice_ref("refs/lively.wav", "我们出发吧！", "活泼"),
        },
        default_voice_id="gentle",
    )

    body = client.get("/media/tts/voices").json()

    assert body["provider"] == "gpt_sovits"
    assert body["server_tts"] is True
    assert body["configured"] is True
    assert body["default_voice"] == "gentle"
    assert body["note"] == ""
    assert [voice["id"] for voice in body["voices"]] == ["gentle", "lively"]
    assert [voice["label"] for voice in body["voices"]] == ["温柔", "活泼"]
    assert [voice["is_default"] for voice in body["voices"]] == [True, False]


def test_voices_endpoint_does_not_leak_server_paths(tmp_path) -> None:
    """参考音频是**服务端**路径，不该下发给前端。"""
    _use_gpt_sovits(tmp_path, voices={"gentle": _voice_ref("refs/secret/x.wav", "", "温柔")})

    body = client.get("/media/tts/voices").json()

    assert set(body["voices"][0]) == {"id", "label", "is_default"}
    assert "refs/secret/x.wav" not in json.dumps(body, ensure_ascii=False)


def test_voices_endpoint_reports_unconfigured_gpt_sovits(tmp_path) -> None:
    """没配参考音频也没音色表：configured=false + 原因（前端据此回落浏览器 TTS）。"""
    _use_gpt_sovits(tmp_path)

    body = client.get("/media/tts/voices").json()

    assert body["server_tts"] is True          # 驱动本身会出音频
    assert body["configured"] is False         # 但还没配好，会降级
    assert "参考音频" in body["note"]


def test_voices_endpoint_never_500_when_provider_is_broken(monkeypatch) -> None:
    """配了 xmov 却没填密钥：面板加载时必须拿到可读状态，而不是 500。"""
    media_module.set_digital_human_provider(None)
    monkeypatch.setenv("DIGITAL_HUMAN_PROVIDER", "xmov")
    monkeypatch.setenv("XMOV_APP_ID", "")
    monkeypatch.setenv("XMOV_SECRET", "")

    resp = client.get("/media/tts/voices")

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "xmov"
    assert body["server_tts"] is False
    assert "不可用" in body["note"]


def test_voices_endpoint_reads_table_from_env_file(tmp_path, monkeypatch) -> None:
    """端到端配置路径：env 指定文件 → 工厂 → provider → 接口。"""
    table = tmp_path / "tts_voices.json"
    table.write_text(
        json.dumps({"gentle": {"ref_audio_path": "refs/g.wav", "label": "温柔"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DIGITAL_HUMAN_PROVIDER", "gpt_sovits")
    monkeypatch.setenv("GPT_SOVITS_VOICES_FILE", str(table))
    monkeypatch.setenv("GPT_SOVITS_DEFAULT_VOICE", "gentle")
    media_module.set_digital_human_provider(None)   # 丢掉上一个用例的 provider

    body = client.get("/media/tts/voices").json()

    assert body["provider"] == "gpt_sovits"
    assert body["configured"] is True
    assert body["default_voice"] == "gentle"
    assert body["voices"][0]["label"] == "温柔"


def test_build_config_maps_every_setting(tmp_path, monkeypatch) -> None:
    """Settings → GptSovitsConfig 的字段映射（漏一个就会静默用错配置）。"""
    table = tmp_path / "voices.json"
    table.write_text(
        json.dumps({"gentle": {"ref_audio_path": "refs/g.wav"}}), encoding="utf-8"
    )
    monkeypatch.setenv("GPT_SOVITS_BASE_URL", "http://192.168.1.9:9880")
    monkeypatch.setenv("GPT_SOVITS_REF_AUDIO", "refs/default.wav")
    monkeypatch.setenv("GPT_SOVITS_PROMPT_TEXT", "默认参考文本")
    monkeypatch.setenv("GPT_SOVITS_PROMPT_LANG", "ja")
    monkeypatch.setenv("GPT_SOVITS_TEXT_LANG", "zh")
    monkeypatch.setenv("GPT_SOVITS_SPEED", "1.25")
    monkeypatch.setenv("GPT_SOVITS_MEDIA_TYPE", "ogg")
    monkeypatch.setenv("GPT_SOVITS_TIMEOUT", "12.5")
    monkeypatch.setenv("GPT_SOVITS_DEFAULT_VOICE", "gentle")
    monkeypatch.setenv("GPT_SOVITS_VOICES_FILE", str(table))

    config = media_module._build_gpt_sovits_config(get_settings())

    assert config.base_url == "http://192.168.1.9:9880"
    assert config.ref_audio_path == "refs/default.wav"
    assert config.prompt_text == "默认参考文本"
    assert config.prompt_lang == "ja"
    assert config.text_lang == "zh"
    assert config.speed == 1.25
    assert config.media_type == "ogg"
    assert config.timeout == 12.5
    assert config.default_voice_id == "gentle"
    assert set(config.voices) == {"gentle"}


# ---------- /media/avatar 产出的音频必须取得到 ----------


def test_avatar_audio_is_served_back(tmp_path) -> None:
    _use_gpt_sovits(tmp_path, ref_audio="refs/default.wav")

    body = client.post("/media/avatar", json={"text": "我会陪着你。", "emotion": "calm"}).json()

    assert body["provider"] == "gpt_sovits"
    assert body["has_audio"] is True
    assert body["audio_format"] == "wav"
    assert body["audio_url"]

    audio = client.get(body["audio_url"])

    assert audio.status_code == 200
    assert audio.content == _wav(2.0)
    assert abs(body["duration_ms"] - 2000) <= 1


def test_audio_route_serves_aac_but_still_rejects_raw(tmp_path, isolated_media_dir) -> None:
    """aac 是 GPT-SoVITS 的合法输出容器；raw（裸 PCM）仍然拒绝。"""
    isolated_media_dir.mkdir(parents=True, exist_ok=True)
    (isolated_media_dir / "tts_x.aac").write_bytes(b"\x00\x01" * 8)
    (isolated_media_dir / "tts_x.raw").write_bytes(b"\x00\x01" * 8)

    assert client.get("/media/audio/tts_x.aac").status_code == 200
    assert client.get("/media/audio/tts_x.raw").status_code == 400

# ---------- 情绪 → 音色映射的对外可见性 ----------


def test_voices_endpoint_flags_emotion_map(tmp_path) -> None:
    """前端要据此说明"不选音色时的行为"，所以必须如实上报有没有映射。"""
    _use_gpt_sovits(
        tmp_path,
        voices={"gentle": _voice_ref("refs/g.wav", "", "温柔"), "lively": _voice_ref("refs/l.wav", "", "活泼")},
    )
    media_module.set_digital_human_provider(
        GptSovitsDigitalHumanProvider(
            config=GptSovitsConfig(
                ref_audio_path="refs/g.wav",
                default_voice_id="gentle",
                voices={"gentle": _voice_ref("refs/g.wav", "", "温柔"), "lively": _voice_ref("refs/l.wav", "", "活泼")},
                emotion_voices={"sad": "lively"},
            ),
            media_dir=tmp_path / "media",
            client=FakeClient(TtsAudio(_wav(1.0), "wav", 1.0)),
        )
    )

    assert client.get("/media/tts/voices").json()["emotion_voices"] is True


def test_voices_endpoint_emotion_map_false_by_default(tmp_path) -> None:
    _use_gpt_sovits(tmp_path, ref_audio="refs/default.wav")

    assert client.get("/media/tts/voices").json()["emotion_voices"] is False


def test_build_config_loads_emotion_map_from_env_file(tmp_path, monkeypatch) -> None:
    """端到端配置路径：音色表里的 _emotion_map 必须真的进到驱动配置里。"""
    table = tmp_path / "voices.json"
    table.write_text(
        json.dumps(
            {
                "gentle": {"ref_audio_path": "refs/g.wav"},
                "lively": {"ref_audio_path": "refs/l.wav"},
                "_emotion_map": {"SAD": "lively", "bad": "not-exist"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GPT_SOVITS_VOICES_FILE", str(table))
    monkeypatch.setenv("GPT_SOVITS_DEFAULT_VOICE", "gentle")

    config = media_module._build_gpt_sovits_config(get_settings())

    assert config.emotion_voices == {"sad": "lively"}   # 键归一化，无效目标已丢弃
    assert set(config.voices) == {"gentle", "lively"}
    assert "_emotion_map" not in config.voices          # 保留键不能混进音色清单

# ---------- 预热入口与额外参数接线 ----------


def test_warmup_entry_returns_false_for_local_provider() -> None:
    """local 驱动没有预热能力：不是错误，返回 False 即可。"""
    media_module.set_digital_human_provider(LocalDigitalHumanProvider())
    assert media_module.warmup_digital_human_provider() is False


def test_warmup_entry_survives_broken_provider(monkeypatch) -> None:
    """配了 xmov 却没填密钥时，预热不能把启动搞崩（只记日志）。"""
    media_module.set_digital_human_provider(None)
    monkeypatch.setenv("DIGITAL_HUMAN_PROVIDER", "xmov")
    monkeypatch.setenv("XMOV_APP_ID", "")
    monkeypatch.setenv("XMOV_SECRET", "")
    assert media_module.warmup_digital_human_provider() is False


def test_build_config_parses_extra_params(monkeypatch) -> None:
    monkeypatch.setenv("GPT_SOVITS_EXTRA_PARAMS", '{"parallel_infer": false}')
    assert media_module._build_gpt_sovits_config(get_settings()).extra_params == {
        "parallel_infer": False
    }


def test_build_config_ignores_broken_extra_params(monkeypatch, caplog) -> None:
    monkeypatch.setenv("GPT_SOVITS_EXTRA_PARAMS", "not json")
    assert media_module._build_gpt_sovits_config(get_settings()).extra_params == {}


def test_warmup_defaults_to_enabled(monkeypatch) -> None:
    """默认开（预热是低风险优化），且能关掉。"""
    monkeypatch.delenv("GPT_SOVITS_WARMUP", raising=False)
    assert get_settings().gpt_sovits_warmup is True
    monkeypatch.setenv("GPT_SOVITS_WARMUP", "false")
    assert get_settings().gpt_sovits_warmup is False
