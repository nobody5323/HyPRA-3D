"""ASR provider 的纯逻辑测试（不需要装 faster-whisper）。

真正跑模型的部分在 `test_asr_real.py`（依赖缺失时自动跳过）；
这里只验**不依赖模型**的那些判断——它们恰恰是最容易出错、
又最容易被「装了模型所以测不到」掩盖的部分：

- 模型目录解析（放对了地方却加载不了的头号原因）；
- 幻听文本过滤（误杀用户的话比漏掉一句水印严重得多）；
- 错误信息是否「能照做」（用户看到的是这句话，不是栈）。
"""

from pathlib import Path

import pytest

from app.perception.asr import (
    ASR_PROVIDERS,
    AsrError,
    create_asr_provider,
    create_asr_provider_from_settings,
)
from app.perception.asr.faster_whisper_provider import (
    _HALLUCINATION_MAX_CHARS,
    FasterWhisperAsrProvider,
    _looks_like_hallucination,
)
from app.perception.asr.none_provider import NoneAsrProvider


def _make_model_dir(root: Path, model: str = "small") -> Path:
    target = root / model
    target.mkdir(parents=True, exist_ok=True)
    (target / "model.bin").write_bytes(b"fake")
    return target


# ---------- 模型目录解析 ----------


class TestModelResolution:
    def test_prefers_spec_subdirectory(self, tmp_path) -> None:
        """按规格分目录存放（便于同时留着 tiny 和 small）。"""
        expected = _make_model_dir(tmp_path, "small")
        provider = FasterWhisperAsrProvider(model="small", model_dir=str(tmp_path))
        assert provider._resolve_model_ref() == str(expected)

    def test_falls_back_to_flat_directory(self, tmp_path) -> None:
        """用户只放了一份模型、没建规格子目录。"""
        (tmp_path / "model.bin").write_bytes(b"fake")
        provider = FasterWhisperAsrProvider(model="small", model_dir=str(tmp_path))
        assert provider._resolve_model_ref() == str(tmp_path)

    def test_falls_back_to_model_name(self, tmp_path) -> None:
        """本地没有 → 交回模型名。

        注意：这会触发 faster-whisper 去 HuggingFace 下载，而本项目**不静默下载**——
        所以走到这条路径时的失败会被 `_explain_load_failure` 换成一句带放置路径的中文提示。
        """
        provider = FasterWhisperAsrProvider(model="small", model_dir=str(tmp_path))
        assert provider._resolve_model_ref() == "small"

    def test_empty_model_dir_returns_name(self) -> None:
        assert FasterWhisperAsrProvider(model="base")._resolve_model_ref() == "base"

    def test_explicit_model_dir_is_respected(self, tmp_path) -> None:
        """绝对路径原样使用（用户把模型放在别处时不该被拼到数据目录下）。"""
        other = tmp_path / "elsewhere"
        expected = _make_model_dir(other, "tiny")
        provider = FasterWhisperAsrProvider(model="tiny", model_dir=str(other))
        assert provider._resolve_model_ref() == str(expected)


# ---------- 幻听过滤 ----------


class TestHallucinationFilter:
    """Whisper 在没人声的音频上会吐出训练集水印——实测 small + 纯音 → 「字幕by索兰娅」。"""

    @pytest.mark.parametrize(
        "text",
        [
            "字幕by索兰娅",
            "字幕由 Amara.org 社区提供",
            "请不吝点赞、订阅、转发、打赏、支持明镜与点点栏目",
            "请订阅我们的频道",
            "明镜与点点栏目",
            "谢谢观看",
            "Thanks for watching!",
            "Subtitles by the Amara.org community",
            "www.example.com",
        ],
    )
    def test_detects_known_watermarks(self, text: str) -> None:
        assert _looks_like_hallucination(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "我订阅了那个频道",
            "今天工作有点累，晚上想早点休息",
            "谢谢你今天陪我聊这么久",
            "我们周末去看电影吧",
            "帮我看看这个字幕是不是错了",  # 「字幕」不在开头
        ],
    )
    def test_keeps_real_speech(self, text: str) -> None:
        """**误杀用户的话比漏掉一句水印严重得多**——所以判据锚定在开头，且不做关键词包含匹配。"""
        assert _looks_like_hallucination(text) is False

    def test_length_cap_protects_long_text(self) -> None:
        """超长的即使命中套路也不丢：那更像用户真的说了什么。"""
        long_text = "字幕" + "很" * _HALLUCINATION_MAX_CHARS
        assert len(long_text) > _HALLUCINATION_MAX_CHARS
        assert _looks_like_hallucination(long_text) is False

    def test_empty_is_not_hallucination(self) -> None:
        """空串走「没识别到内容」那条路，不该被当成幻听（两者提示语不同）。"""
        assert _looks_like_hallucination("") is False
        assert _looks_like_hallucination("   ") is False


# ---------- 错误信息可照做 ----------


def test_load_failure_message_tells_where_to_put_the_model(tmp_path) -> None:
    """用户看到的是这句话，不是栈——所以它必须写明「放哪」。"""
    provider = FasterWhisperAsrProvider(model="small", model_dir=str(tmp_path))
    message = provider._explain_load_failure(RuntimeError("boom"), "small")

    assert "small" in message
    assert "model.bin" in message
    assert str(tmp_path) in message


def test_load_failure_lists_available_sizes(tmp_path) -> None:
    provider = FasterWhisperAsrProvider(model="small", model_dir=str(tmp_path))
    message = provider._explain_load_failure(RuntimeError("boom"), "small")
    assert "tiny" in message and "large-v3" in message


def test_none_provider_refuses_with_actionable_message() -> None:
    provider = NoneAsrProvider()
    assert provider.available() is False
    with pytest.raises(AsrError, match="ASR_PROVIDER"):
        provider.transcribe(b"audio")


# ---------- 工厂 ----------


@pytest.mark.parametrize("name", ["", "none", "写错了"])
def test_unknown_provider_falls_back_to_none(name: str) -> None:
    """ASR 是可选能力：配置写错只该让麦克风消失，不该让后端起不来。"""
    assert create_asr_provider(name).name == "none"


@pytest.mark.parametrize("name", ["faster-whisper", "faster_whisper", "whisper"])
def test_aliases_resolve_to_faster_whisper(name: str) -> None:
    assert create_asr_provider(name, model="tiny").name == "faster-whisper"


def test_provider_from_settings_reads_vad_options(tmp_path) -> None:
    from app.config import Settings

    settings = Settings(
        asr_provider="faster-whisper",
        asr_model="base",
        asr_model_dir=str(tmp_path),
        asr_vad_filter=False,
        asr_no_speech_threshold=0.8,
    )
    provider = create_asr_provider_from_settings(settings)
    assert isinstance(provider, FasterWhisperAsrProvider)
    assert provider.vad_filter is False
    assert provider.no_speech_threshold == 0.8


def test_registry_lists_expected_names() -> None:
    assert "none" in ASR_PROVIDERS
    assert "faster-whisper" in ASR_PROVIDERS
