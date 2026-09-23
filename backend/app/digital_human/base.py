"""数字人驱动接口。

后端职责：产出「音频 + 口型/表情/动作时间轴」；
前端职责：消费时间轴渲染 3D（魔珐星云 SDK 或 Three.js）。

实现：
- local  本地降级（零依赖，无音频，时间轴按文本估算）——默认，评审无 key 亦可用
- xmov   魔珐星云（TTS WebSocket 取音频 + 字级时间戳，口型精确对齐）
- gpt_sovits  GPT-SoVITS（自部署 TTS 取整段音频，口型按音频时长缩放估算）
"""

from abc import ABC, abstractmethod

from app.digital_human.models import AvatarOutput


class DigitalHumanProvider(ABC):
    """数字人驱动抽象。"""

    #: 提供方名称（local / xmov）
    name: str = "base"

    @abstractmethod
    def synthesize(
        self,
        text: str,
        *,
        emotion: str | None = None,
        expression: str | None = None,
        intensity: float = 0.5,
        voice: str | None = None,
    ) -> AvatarOutput:
        """把一段文本 + 情绪合成为数字人驱动数据。

        参数:
            text: 要"说"的文本（通常是 assistant 回复）；
            emotion: 情绪标签（英文，如 anxious）；
            expression: 表情键（如 frowning_worry），缺省由 emotion 推导；
            intensity: 情绪强度 0-1（驱动表情与动作幅度）；
            voice: 音色 ID（不同提供方取值不同，缺省用配置默认）。
        """
