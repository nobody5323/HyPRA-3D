"""ASR provider 工厂（`(name, **kwargs) -> 实现`，与 §9.1 既有扩展点同范式）。"""

from __future__ import annotations

from app.perception.asr.base import AsrError, AsrProvider, AsrResult
from app.perception.asr.faster_whisper_provider import FasterWhisperAsrProvider
from app.perception.asr.none_provider import NoneAsrProvider

#: 实现名 → 构造器
ASR_PROVIDERS = {
    "none": NoneAsrProvider,
    "faster-whisper": FasterWhisperAsrProvider,
    # 别名：配置里写 faster_whisper / whisper 都能用（手写 .env 时最容易打错的两种）
    "faster_whisper": FasterWhisperAsrProvider,
    "whisper": FasterWhisperAsrProvider,
}


def create_asr_provider(name: str = "", **kwargs) -> AsrProvider:
    """按实现名创建 ASR provider；空名或未知名一律回落 `none`。

    未知名**回落而不是报错**：ASR 是可选能力，配置写错只该让麦克风消失
    （界面上看得见），不该让后端起不来或让对话挂掉。
    """
    key = (name or "").strip().lower()
    factory = ASR_PROVIDERS.get(key)
    if factory is None:
        if key:
            # 只有「用户填了东西但填错」才告警；空串是默认值，不刷日志
            import logging

            logging.getLogger(__name__).warning(
                "未知 ASR_PROVIDER：%r，已回落 none（可选：%s）",
                name,
                " / ".join(sorted(set(ASR_PROVIDERS))),
            )
        return NoneAsrProvider()
    if factory is NoneAsrProvider:
        return NoneAsrProvider()
    return factory(**kwargs)


def create_asr_provider_from_settings(settings) -> AsrProvider:
    """按 `Settings` 构造 ASR provider（唯一读取配置的地方）。

    集中在这里的理由：配置项有 7 个，散在 API 层与预热任务里各读一遍，
    改一个字段就要同步两处——迟早漏。
    """
    return create_asr_provider(
        settings.asr_provider,
        model=settings.asr_model,
        model_dir=settings.asr_model_dir,
        device=settings.asr_device,
        compute_type=settings.asr_compute_type,
        beam_size=settings.asr_beam_size,
        download_root=settings.asr_download_root,
        vad_filter=settings.asr_vad_filter,
        no_speech_threshold=settings.asr_no_speech_threshold,
    )


__all__ = [
    "ASR_PROVIDERS",
    "AsrError",
    "AsrProvider",
    "AsrResult",
    "create_asr_provider",
    "create_asr_provider_from_settings",
]
