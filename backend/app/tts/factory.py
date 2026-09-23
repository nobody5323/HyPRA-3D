"""TTS 工厂：按名字创建实现。

**不引入新的配置开关**：现状由 `DIGITAL_HUMAN_PROVIDER=gpt_sovits` 决定「要不要服务端
语音」。再加一个 `TTS_PROVIDER` 就是两个开关说同一件事——迟早漂移成
「一个开一个关」的谜题。工厂的按名创建是给**能力注册表与第三方**用的。
"""

from __future__ import annotations

from app.tts.base import NullTtsProvider, TtsProvider
from app.tts.gpt_sovits import GptSovitsClient, GptSovitsConfig
from app.tts.gpt_sovits_provider import GptSovitsTtsProvider

NONE = "none"
GPT_SOVITS = "gpt_sovits"

#: 可选值（供报错信息与文档引用）
TTS_PROVIDERS: tuple[str, ...] = (NONE, GPT_SOVITS)

#: GPT-SoVITS 的名称别名（集中一处：本模块是**唯一**的事实来源，
#: `digital_human/factory.py` 从这里取，避免两处各写一份而漂移）
GPT_SOVITS_NAMES = frozenset({"gpt_sovits", "gpt-sovits", "gptsovits"})

#: 显式关闭的别名
_NONE_ALIASES = frozenset({"none", "off", "disabled", "no", ""})


def create_tts_provider(
    name: str = "",
    *,
    config: GptSovitsConfig | None = None,
    client: GptSovitsClient | None = None,
) -> TtsProvider:
    """按名字创建语音合成实现。

    空名与 `none` 都返回 `NullTtsProvider`（不做合成），而不是抛错——
    「不配 TTS」是完全正常的部署形态。

    抛出:
        ValueError: 名字未知。
    """
    key = (name or "").strip().lower()

    if key in _NONE_ALIASES:
        return NullTtsProvider()
    if key in GPT_SOVITS_NAMES:
        return GptSovitsTtsProvider(config, client=client)
    raise ValueError(f"未知 TTS 实现：{name!r}（可选 {' | '.join(TTS_PROVIDERS)}）")
