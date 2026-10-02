"""视觉理解子包：Qwen2.5-VL（用户主动分享图片）。

见 `docs/proactive-multimodal.md` §4.4。**不做摄像头、不做连续屏幕感知**。
"""

from app.perception.vision.base import VisionError, VisionProvider, VisionResult
from app.perception.vision.factory import (
    VISION_PROVIDERS,
    create_vision_provider,
    create_vision_provider_from_settings,
)

__all__ = [
    "VISION_PROVIDERS",
    "VisionError",
    "VisionProvider",
    "VisionResult",
    "create_vision_provider",
    "create_vision_provider_from_settings",
]
