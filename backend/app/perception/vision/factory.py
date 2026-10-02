"""视觉 provider 工厂（`(name, **kwargs) -> 实现`，与 §9.1 既有扩展点同范式）。"""

from __future__ import annotations

import logging

from app.perception.vision.base import VisionError, VisionProvider, VisionResult
from app.perception.vision.none_provider import NoneVisionProvider
from app.perception.vision.openai_compatible import OpenAiCompatibleVisionProvider

logger = logging.getLogger(__name__)

#: 实现名 → 构造器
VISION_PROVIDERS = {
    "none": NoneVisionProvider,
    "openai-compatible": OpenAiCompatibleVisionProvider,
    # 别名：配置里写 qwen-vl / qwen2.5-vl / dashscope 都能用
    # （手写 .env 时最容易打错的三种写法）
    "qwen-vl": OpenAiCompatibleVisionProvider,
    "qwen2.5-vl": OpenAiCompatibleVisionProvider,
    "dashscope": OpenAiCompatibleVisionProvider,
    "siliconflow": OpenAiCompatibleVisionProvider,
}


def create_vision_provider(name: str = "", **kwargs) -> VisionProvider:
    """按实现名创建视觉 provider；空名或未知名一律回落 `none`。

    未知名**回落而不是报错**：视觉是可选能力，配置写错只该让「发图」入口消失
    （界面上看得见），不该让后端起不来或让对话挂掉。
    """
    key = (name or "").strip().lower()
    factory = VISION_PROVIDERS.get(key)
    if factory is None:
        if key:
            logger.warning(
                "未知 VISION_PROVIDER：%r，已回落 none（可选：%s）",
                name,
                " / ".join(sorted(set(VISION_PROVIDERS))),
            )
        return NoneVisionProvider()
    if factory is NoneVisionProvider:
        return NoneVisionProvider()
    return factory(**kwargs)


def create_vision_provider_from_settings(settings) -> VisionProvider:
    """按 `Settings` 构造视觉 provider（**唯一读取配置的地方**）。

    集中在这里的理由与 ASR 相同：配置项有 6 个，散在 API 层各读一遍，
    改一个字段就要同步两处——迟早漏。

    `provider_name` 取 `VISION_PROVIDER` 本身（`dashscope` / `siliconflow` 这两个
    别名值同时充当「用哪家的默认端点」），因此用户只填一个字段就能跑通。
    """
    return create_vision_provider(
        settings.vision_provider,
        api_key=settings.vision_api_key,
        model=settings.vision_model,
        base_url=settings.vision_base_url,
        provider_name=(settings.vision_provider or "").strip().lower(),
        timeout=settings.vision_timeout,
        prompt=settings.vision_prompt,
    )


__all__ = [
    "VISION_PROVIDERS",
    "VisionError",
    "VisionProvider",
    "VisionResult",
    "create_vision_provider",
    "create_vision_provider_from_settings",
]
