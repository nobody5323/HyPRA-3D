"""`none`：不做图片理解（默认实现）。

与 ASR 的 `none` 同一口径：它的作用是让**降级可见**——
`GET /perception/status` 明确回 `vision_available=false`，
界面据此**不渲染「发图」入口**。比「按钮在、点了报错」友好得多。
"""

from __future__ import annotations

from app.perception.vision.base import VisionError, VisionProvider, VisionResult


class NoneVisionProvider(VisionProvider):
    """未启用图片理解。"""

    name = "none"

    def available(self) -> bool:
        return False

    def describe(
        self, image: bytes, *, mime: str = "image/png", question: str = ""
    ) -> VisionResult:
        raise VisionError(
            "图片理解未启用。请在「能力中心 → 图片理解」里选择 Qwen2.5-VL 并填入 API Key，"
            "或设置 VISION_PROVIDER=openai-compatible。"
        )
