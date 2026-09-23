"""TTS 接口测试（`AGENTS.md §9.10` 第 12 项）。

重点在**接口与实现的分工**：

1. 接口不替调用方吞错误（失败抛异常，要不要降级是调用方的决定）；
2. 调用方只依赖接口——换 TTS 实现不必改数字人驱动；
3. 别名只有一份事实来源（数字人工厂从 tts 工厂取，不各写一份）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tts import (
    GPT_SOVITS_NAMES,
    NullTtsProvider,
    TtsAudio,
    TtsError,
    TtsNotAvailable,
    create_tts_provider,
)


def test_tts_audio_reports_duration_availability() -> None:
    assert TtsAudio(b"x", "wav", 1.5).has_duration is True
    # 裸 PCM / 非 WAV 拿不到容器头 → 调用方按估算兜底
    assert TtsAudio(b"x", "raw", None).has_duration is False
    assert TtsAudio(b"x", "raw", 0.0).has_duration is False


def test_empty_and_none_names_mean_no_synthesis() -> None:
    """空名与 none / off / disabled 都表示「不做合成」，而不是报错——

    不配 TTS 是完全正常的部署形态（前端回落浏览器 TTS）。
    """
    for name in ("", "none", "off", "disabled", "NONE"):
        provider = create_tts_provider(name)
        assert isinstance(provider, NullTtsProvider), name
        assert provider.available() is False


def test_null_provider_raises_on_synthesis() -> None:
    """关闭 TTS 时合成要抛**明确的**异常，而不是返回空音频。

    返回空音频会让调用方以为「合成成功但没声音」，降级路径永远走不到。
    """
    with pytest.raises(TtsNotAvailable):
        create_tts_provider("none").synthesize("你好")


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="未知 TTS 实现"):
        create_tts_provider("edge")


def test_gpt_sovits_names_have_one_source_of_truth() -> None:
    """别名只有一份事实来源：数字人工厂从这里取，不各写一份。"""
    from app.digital_human.factory import GPT_SOVITS_NAMES as digital_human_aliases

    assert digital_human_aliases is GPT_SOVITS_NAMES
    assert {"gpt_sovits", "gpt-sovits"} <= GPT_SOVITS_NAMES


def test_gpt_sovits_error_is_a_tts_error() -> None:
    """调用方只需 `except TtsError` 就能覆盖所有实现，不必记住各家异常类名。"""
    from app.tts.gpt_sovits import GptSovitsError

    assert issubclass(GptSovitsError, TtsError)
    # 也仍是 RuntimeError（既有调用方可能按它捕获）
    assert issubclass(GptSovitsError, RuntimeError)


def test_digital_human_provider_uses_injected_tts(tmp_path: Path) -> None:
    """数字人驱动经 `TtsProvider` 取音频——换 TTS 不必改驱动。"""
    from app.digital_human.gpt_sovits_provider import GptSovitsDigitalHumanProvider
    from app.tts.base import TtsProvider

    class StubTts(TtsProvider):
        name = "stub"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def synthesize(self, text: str, **overrides) -> TtsAudio:  # noqa: ARG002
            self.calls.append(text)
            return TtsAudio(b"\x00" * 100, "wav", 2.0)

    stub = StubTts()
    provider = GptSovitsDigitalHumanProvider(media_dir=tmp_path, tts=stub)

    output = provider.synthesize("你好", emotion="happy")

    assert stub.calls == ["你好"]
    assert output.provider == "gpt_sovits"
    assert output.audio_path is not None      # 音频已落盘


def test_digital_human_provider_degrades_when_tts_fails(tmp_path: Path) -> None:
    """TTS 失败 → 降级到本地（不阻断对话），并把原因写进 meta。"""
    from app.digital_human.gpt_sovits_provider import GptSovitsDigitalHumanProvider
    from app.tts.base import TtsProvider

    class FailingTts(TtsProvider):
        name = "failing"

        def synthesize(self, text: str, **overrides) -> TtsAudio:  # noqa: ARG002
            raise TtsError("服务没起来")

    provider = GptSovitsDigitalHumanProvider(media_dir=tmp_path, tts=FailingTts())
    output = provider.synthesize("你好")

    assert output.meta["degraded_from"] == "gpt_sovits"
    assert "服务没起来" in output.meta["degrade_reason"]
    assert output.audio_path is None
