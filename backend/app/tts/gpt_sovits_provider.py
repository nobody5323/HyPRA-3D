"""GPT-SoVITS 的 `TtsProvider` 实现。

**薄包装**：只把既有的 `GptSovitsClient` 接到 `TtsProvider` 接口上，不复制它的
请求构造与参数处理——那边已经有 20+ 项测试覆盖，重写一遍只会引入新的失败面。
"""

from __future__ import annotations

from typing import Any

from app.tts.base import TtsAudio, TtsProvider
from app.tts.gpt_sovits import GptSovitsClient, GptSovitsConfig


class GptSovitsTtsProvider(TtsProvider):
    """GPT-SoVITS（官方 api_v2.py）实现。"""

    name = "gpt_sovits"

    def __init__(
        self,
        config: GptSovitsConfig | None = None,
        *,
        client: GptSovitsClient | None = None,
    ) -> None:
        """
        参数:
            config: 服务地址 / 默认参考音频 / 音色表；缺省用默认值
                （此时调用会因缺参考音频而失败，由上层降级）；
            client: 注入客户端（测试用；缺省按 config 构造）。
        """
        self.config = config or GptSovitsConfig()
        self.client = client or GptSovitsClient(
            base_url=self.config.base_url,
            ref_audio_path=self.config.ref_audio_path,
            prompt_text=self.config.prompt_text,
            prompt_lang=self.config.prompt_lang,
            text_lang=self.config.text_lang,
            speed=self.config.speed,
            media_type=self.config.media_type,
            timeout=self.config.timeout,
            extra_params=self.config.extra_params,
        )

    def available(self) -> bool:
        """配了服务地址即视为就绪（真正的可用性由一次调用决定）。"""
        return bool(self.config.base_url)

    def synthesize(self, text: str, **overrides: Any) -> TtsAudio:
        """同步合成（异常向上传播——`GptSovitsError` 已是 `TtsError` 的子类）。"""
        return self.client.synthesize_sync(text, **overrides)
