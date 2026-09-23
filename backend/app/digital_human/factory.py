"""数字人驱动工厂：按配置创建 provider。"""

from app.digital_human.base import DigitalHumanProvider
from app.digital_human.gpt_sovits_provider import GptSovitsDigitalHumanProvider
from app.digital_human.local_provider import LocalDigitalHumanProvider
from app.digital_human.xmov_provider import XmovDigitalHumanProvider
from app.tts.factory import GPT_SOVITS_NAMES
from app.tts.gpt_sovits import GptSovitsConfig

#: gpt_sovits 的名称别名——**从 tts 工厂取，不在这里另写一份**：
#: `create_tts_provider()` 也要用它，两处各写一份迟早漂移成
#: 「配置能起 TTS，但数字人这条路径不认这个值」。
__all__ = ["GPT_SOVITS_NAMES", "create_digital_human_provider"]


def create_digital_human_provider(
    provider: str = "local",
    *,
    app_id: str = "",
    secret: str = "",
    voice: str = "",
    host: str = "",
    media_dir: str = "media",
    gpt_sovits: GptSovitsConfig | None = None,
    fallback: DigitalHumanProvider | None = None,
) -> DigitalHumanProvider:
    """按名称创建数字人驱动 provider。

    参数:
        provider: local（零依赖降级，默认）| xmov（魔珐星云）| gpt_sovits（GPT-SoVITS TTS）；
        app_id / secret: 魔珐控制台密钥（xmov 时必填）；
        voice: 魔珐音色 ID（tts_vcn）；
        host: 魔珐服务主机；
        media_dir: 音频落盘目录；
        gpt_sovits: GPT-SoVITS 配置（服务地址 / 默认音色 / 音色表）；
        fallback: xmov / gpt_sovits 不可用时的降级实现（默认本地）。
    """
    name = (provider or "local").strip().lower()
    if name in {"local", "mock", "inmemory"}:
        return LocalDigitalHumanProvider()
    if name == "xmov":
        return XmovDigitalHumanProvider(
            app_id=app_id,
            secret=secret,
            voice=voice or "",
            host=host or "",
            media_dir=media_dir,
            fallback=fallback,
        )
    if name in GPT_SOVITS_NAMES:
        return GptSovitsDigitalHumanProvider(
            config=gpt_sovits,
            media_dir=media_dir,
            fallback=fallback,
        )
    raise ValueError(f"未知数字人 provider：{provider!r}（可选 local | xmov | gpt_sovits）")
