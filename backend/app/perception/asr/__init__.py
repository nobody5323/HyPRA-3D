"""语音识别（ASR）子包：本地 faster-whisper 优先。

见 `docs/proactive-multimodal.md` §4.3。
"""

from app.perception.asr.base import AsrError, AsrProvider, AsrResult
from app.perception.asr.factory import (
    ASR_PROVIDERS,
    create_asr_provider,
    create_asr_provider_from_settings,
)

__all__ = [
    "ASR_PROVIDERS",
    "AsrError",
    "AsrProvider",
    "AsrResult",
    "create_asr_provider",
    "create_asr_provider_from_settings",
]
