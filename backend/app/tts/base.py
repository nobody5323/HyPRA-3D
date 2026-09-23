"""TTS 接口：文本 → 音频（`AGENTS.md §9.10` 第 12 项）。

**与 `app/digital_human/` 的分工**（既有约定，本模块把它显式化为接口）：

- TTS 只负责**拿到音频**；
- `digital_human` 负责把音频 + 情绪组装成**驱动时间轴**（口型 / 表情 / 动作）。

为什么把它从数字人驱动里分出来：魔珐星云自带 TTS（且能返回**字级时间戳**），
而 GPT-SoVITS 是**独立服务**、且只返回整段音频——两者的能力不同。
用一层薄接口隔离「怎么取音频」，驱动层就只需关心「拿到音频后怎么对齐口型」。

**拆成独立能力的直接收益**：可替换性变得可见。将来的 Edge-TTS / Azure / 自研 TTS
只需实现本接口并注册，不必再往 `digital_human` 的 provider 里塞一个分支
（那正是 `gpt_sovits` 当初的处境）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

#: 默认输出音频格式（建议 wav：浏览器 `<audio>` 可直接播放）
DEFAULT_MEDIA_TYPE = "wav"


class TtsError(RuntimeError):
    """语音合成失败（服务未启动 / 超时 / 参数被拒 / 空音频等）。

    上层据此降级（不阻断对话与渲染）。
    """


class TtsNotAvailable(TtsError):
    """当前没有任何可用的语音合成（如配了 `none`）。"""


@dataclass
class TtsAudio:
    """一次语音合成的产物（与魔珐的 `TtsResult` 区分：本类**没有**字级时间戳）。"""

    audio_bytes: bytes
    audio_format: str = DEFAULT_MEDIA_TYPE
    #: 时长（秒）；容器头拿不到时为 None（非 WAV / 裸 PCM），由调用方按估算兜底
    duration_s: float | None = None

    @property
    def has_duration(self) -> bool:
        return bool(self.duration_s and self.duration_s > 0)


class TtsProvider(ABC):
    """语音合成抽象。"""

    #: 实现名（与配置值一致，供调试与能力索引展示）
    name: str = ""

    def available(self) -> bool:
        """是否就绪（没配服务地址等）。默认就绪。"""
        return True

    @abstractmethod
    def synthesize(self, text: str, **overrides: Any) -> TtsAudio:
        """合成一段音频。

        `overrides` 的取值各家不同（GPT-SoVITS 是参考音频 / 提示文本 / 语言…），
        由调用方按当前实现透传——接口刻意**不**把它们写成固定字段，
        否则每接一家新服务都要改接口本身。

        失败应**抛异常**（`TtsError` 及其子类）：要不要降级是调用方的决定，
        接口不替它吞掉错误。
        """


class NullTtsProvider(TtsProvider):
    """不做语音合成。

    存在理由：**显式关闭服务端 TTS**（前端回落浏览器 TTS、或只做字幕）是一个真实
    选择，需要有个实现能表达它——否则「没配」与「配错了」在链路上无法区分。
    """

    name = "none"

    def available(self) -> bool:
        return False

    def synthesize(self, text: str, **overrides: Any) -> TtsAudio:  # noqa: ARG002
        raise TtsNotAvailable("未启用服务端语音合成（tts=none）")
