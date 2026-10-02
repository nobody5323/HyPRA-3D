"""`none`：不做语音识别（默认实现）。

存在意义不是「占位」，而是让**降级可见**：`ASR_PROVIDER=none` 时
`GET /perception/status` 会明确回 `available=false`，界面据此**不渲染麦克风**。
比「按钮在、点了报错」友好得多——后者会被用户读成「功能坏了」。
"""

from __future__ import annotations

from app.perception.asr.base import AsrError, AsrProvider, AsrResult


class NoneAsrProvider(AsrProvider):
    """未启用语音识别。"""

    name = "none"

    def available(self) -> bool:
        return False

    def transcribe(
        self, audio: bytes, *, language: str = "zh", filename: str = "audio.webm"
    ) -> AsrResult:
        raise AsrError(
            "语音识别未启用。请在「能力中心 → 语音识别」里选择 faster-whisper 并保存，"
            "或设置 ASR_PROVIDER=faster-whisper。"
        )
