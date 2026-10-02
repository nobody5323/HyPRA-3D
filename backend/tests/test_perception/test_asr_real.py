"""真实语音识别联调（依赖缺失时自动跳过）。

设计见 `docs/proactive-multimodal.md` §4.3。分三层，每层跳过条件不同：

| 用例 | 前置 | 验什么 |
| --- | --- | --- |
| 依赖与模型就位 | 装了 faster-whisper + 模型在 `ASR_MODEL_DIR` | 配置 → provider 的解析链路 |
| 合成音频跑通 | 同上 | **模型加载 + 推理链路**（含音频解码） |
| 真实语音转写 | 额外给一个语音样本路径 | 中文识别质量 |

第三层需要真实语音样本，而样本属**用户本地数据**（不进仓库、不随发行物分发，
见 AGENTS.md §6），因此走环境变量传入：

    HYPRA_ASR_FIXTURE_WAV=/path/to/speech.wav ../.venv/Scripts/python.exe \
        -m pytest tests/test_perception/test_asr_real.py -v

⚠️ 本文件**不联网**：模型必须已经在本地。faster-whisper 在只给模型名时会去
HuggingFace 拉权重，而本项目刻意不静默下载（见 provider 的模块文档）——
所以这里显式要求本地目录里能找到 `model.bin`。
"""

import importlib.util
import io
import math
import os
import struct
import wave

import pytest

from app.config import Settings
from app.perception.asr import create_asr_provider_from_settings
from app.perception.asr.faster_whisper_provider import _MODEL_MARKER

_HAS_DEPENDENCY = importlib.util.find_spec("faster_whisper") is not None


def _settings() -> Settings:
    return Settings(
        asr_provider="faster-whisper",
        asr_model=os.getenv("HYPRA_ASR_MODEL", "tiny"),
        asr_model_dir=os.getenv("HYPRA_ASR_MODEL_DIR", "models/whisper"),
        asr_device="cpu",
        asr_compute_type="int8",
    )


def _model_present(settings: Settings) -> bool:
    from pathlib import Path

    root = Path(settings.asr_model_dir)
    return (root / settings.asr_model / _MODEL_MARKER).is_file() or (
        root / _MODEL_MARKER
    ).is_file()


def _tone_wav(seconds: float = 1.5, rate: int = 16000) -> bytes:
    """合成一段正弦音（**没有语音内容**）。

    用途是验「模型能加载 + 音频能解码 + 推理不报错」——
    内容为空正是期望结果（`warnings` 里会说明没识别到内容）。
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(rate * seconds))
        )
        writer.writeframes(frames)
    return buffer.getvalue()


requires_dependency = pytest.mark.skipif(
    not _HAS_DEPENDENCY,
    reason="未安装 faster-whisper（pip install -i 官方源 '.[asr]'，见 pyproject.toml）",
)


@requires_dependency
def test_provider_resolves_local_model_path() -> None:
    """配置里的相对路径必须解析成绝对路径，且指向真正的模型目录。

    这是「模型放对了地方却加载不了」这一类问题的第一道排查点：
    `ASR_MODEL_DIR` 相对路径按**数据目录**解析（开发形态是 backend/），
    不是按 cwd——写成相对路径时两者恰好一致，装成包之后就不一致了。
    """
    settings = _settings()
    if not _model_present(settings):
        pytest.skip(f"模型不在 {settings.asr_model_dir}/{settings.asr_model}/")

    provider = create_asr_provider_from_settings(settings)
    assert provider.available() is True
    resolved = provider._resolve_model_ref()  # noqa: SLF001 - 就是要验这个内部解析
    assert os.path.isabs(resolved)
    assert os.path.isfile(os.path.join(resolved, _MODEL_MARKER))


@requires_dependency
def test_real_transcribe_on_synthetic_audio() -> None:
    """真实跑一遍模型：加载 → 解码 → 推理，全程不抛异常。

    内容为空是**期望结果**（正弦音里没有说话），关键是被如实报成
    「没有识别到语音内容」而不是当成失败。
    """
    settings = _settings()
    if not _model_present(settings):
        pytest.skip(f"模型不在 {settings.asr_model_dir}/{settings.asr_model}/")

    provider = create_asr_provider_from_settings(settings)
    provider.warmup()  # 预热失败不影响本用例：transcribe 会再试一次并给完整原因

    result = provider.transcribe(_tone_wav(), language="zh")
    assert result.provider == "faster-whisper"
    assert result.duration_seconds > 0  # 音频时长被正确解析出来了
    assert result.text.strip() == ""
    assert result.warnings


@requires_dependency
def test_real_transcribe_on_speech_fixture() -> None:
    """真实语音 → 文本（需要样本路径）。

    样本**不进仓库**（属用户本地数据），因此走环境变量传入。
    """
    fixture = os.getenv("HYPRA_ASR_FIXTURE_WAV", "").strip()
    if not fixture:
        pytest.skip("未提供语音样本：设置 HYPRA_ASR_FIXTURE_WAV=<path>")
    if not os.path.isfile(fixture):
        pytest.skip(f"语音样本不存在：{fixture}")

    settings = _settings()
    if not _model_present(settings):
        pytest.skip(f"模型不在 {settings.asr_model_dir}/{settings.asr_model}/")

    provider = create_asr_provider_from_settings(settings)
    with open(fixture, "rb") as handle:
        result = provider.transcribe(handle.read(), language="zh")

    # 只断言「识别出了内容」，不断言具体文字：
    # 断言具体文字会把测试绑死在某个模型规格上（tiny 与 small 的结果不同），
    # 而这条用例要验的是**链路通不通**。
    assert result.text.strip(), f"未能从样本中识别出文本：{result.warnings}"
