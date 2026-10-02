"""Qwen2.5-VL（OpenAI 兼容端点）：把用户分享的图片读成中性事实。

## 为什么走 OpenAI 兼容而不是 DashScope 原生 SDK

阿里百炼与硅基流动都提供 OpenAI 兼容的多模态端点（`image_url` 传 data URL），
而 `openai` SDK **已经是既有依赖**——于是「引入 Qwen2.5-VL」这件事
新增的依赖数是 **0**（见 `AGENTS.md §5`：新增依赖前要先列出方案）。

## 两条实现纪律

1. **图片以 data URL 内联，不落盘**（§4.6 红线）。base64 进请求体，
   后端不留副本——「用户分享的图会不会被存下来」这个问题，
   最好的答案是没有那个文件。
2. **提示词要求「只描述看得见的东西」**。视觉模型天然爱做推断
   （「他看起来很疲惫」），而推断属于人设的职责。感知层只提供素材，
   把解读权留给模型——与人设一致的情绪判断才是「这个角色的解读」。
"""

from __future__ import annotations

import base64
import logging

from app.perception.vision.base import VisionError, VisionProvider, VisionResult

logger = logging.getLogger(__name__)

#: 各提供商默认 base_url（与 `app/llm/openai_compatible.py` 同一套口径）
DEFAULT_BASE_URLS: dict[str, str] = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}

#: 默认的「客观描述」指令。
#:
#: 三条要求各有针对性：
#: - **只描述看得见的**：压住模型的推断冲动（推断归人设）；
#: - **不超过 N 行**：感知层有独立预算，长描述会挤掉记忆；
#: - **不要加建议/安慰**：那些是回复的事，不是感知的事。
DEFAULT_VISION_PROMPT = (
    "请客观描述这张图片里看得见的内容，供对话伙伴参考。要求：\n"
    "1. 只描述**看得见的东西**（画面主体、场景、文字、明显的颜色与氛围），"
    "不要推断人物的心理状态或编造背景故事；\n"
    "2. 用 1~3 句短句，不要分点、不要标题；\n"
    "3. 不要写任何建议、安慰或评论。"
)


class OpenAiCompatibleVisionProvider(VisionProvider):
    """通过 openai SDK 调用任意 OpenAI 兼容的多模态端点（Qwen2.5-VL）。"""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "qwen2.5-vl-72b-instruct",
        base_url: str = "",
        provider_name: str = "dashscope",
        timeout: float = 60.0,
        max_retries: int = 1,
        prompt: str = "",
        http_client=None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.base_url = (
            (base_url or "").strip() or DEFAULT_BASE_URLS.get(provider_name, "")
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.prompt = (prompt or "").strip() or DEFAULT_VISION_PROMPT
        self._http_client = http_client
        self._client = None

    # ---------- 客户端 ----------

    def _resolve_client(self):
        """惰性创建客户端（未配置时不创建，避免启动即报错）。"""
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise VisionError(
                "图片理解未配置 API Key。请在「能力中心 → 图片理解」里填写，"
                "或设置 VISION_API_KEY。"
            )
        if not self.base_url:
            raise VisionError(
                "图片理解未配置端点地址。请设置 VISION_BASE_URL"
                "（阿里百炼：https://dashscope.aliyuncs.com/compatible-mode/v1；"
                "硅基流动：https://api.siliconflow.cn/v1）。"
            )
        from openai import OpenAI  # noqa: PLC0415 - 惰性导入，未启用时不付启动成本

        kwargs: dict = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "max_retries": self.max_retries,
        }
        if self._http_client is not None:
            kwargs["http_client"] = self._http_client
        self._client = OpenAI(**kwargs)
        return self._client

    def available(self) -> bool:
        """已配置 key、模型与端点。**不发起请求**——状态查询是高频接口。"""
        return bool(self.api_key and self.model and self.base_url)

    # ---------- 调用 ----------

    def describe(
        self, image: bytes, *, mime: str = "image/png", question: str = ""
    ) -> VisionResult:
        if not image:
            raise VisionError("图片内容为空。")
        client = self._resolve_client()

        # 图片以 data URL 内联：**不落盘**（§4.6）。用户分享的图不该在后端留下副本。
        encoded = base64.b64encode(image).decode("ascii")
        data_url = f"data:{mime or 'image/png'};base64,{encoded}"

        instruction = self.prompt
        if question.strip():
            # 用户带了问题：仍然要求「只描述看得见的」，但把关注点让给用户的问题。
            # 不直接把问题当指令用——那样模型会开始「回答问题」而不是「描述画面」，
            # 于是感知事实里混进了一段对话内容，注入提示词后与人设的回答撞车。
            instruction = (
                f"{self.prompt}\n\n"
                f"用户随图问了一句：「{question.strip()}」——"
                "请围绕这个问题所指向的画面内容来描述，仍然不要直接回答或给建议。"
            )

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": instruction},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                max_tokens=300,
            )
        except VisionError:
            raise
        except Exception as exc:  # noqa: BLE001 - 换成能照做的中文提示
            raise VisionError(
                f"图片理解失败：{exc}\n"
                f"（模型 {self.model}，端点 {self.base_url}）"
                "常见原因：API Key 无效、模型名不对、或该模型不支持图片输入。"
            ) from exc

        text = ""
        choices = getattr(response, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            text = (getattr(message, "content", "") or "").strip()

        if not text:
            return VisionResult(
                description="",
                provider=self.name,
                model=self.model,
                warnings=["模型没有给出图片描述。"],
            )
        return VisionResult(description=text, provider=self.name, model=self.model)
