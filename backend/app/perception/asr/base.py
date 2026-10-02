"""ASR（语音识别）：provider 接口与结果模型。

设计见 `docs/proactive-multimodal.md` §4.3。已决策：**本地 faster-whisper**。

| provider | 依赖 | 状态 |
| --- | --- | --- |
| `none` | — | **默认**。整体禁用：界面上不出现麦克风（不是「点了没反应」） |
| `faster-whisper` | 新增依赖 | 本次采用：离线、免费、语音数据不出本机 |
| `openai_compatible` | 零新依赖 | 保留位，本期不实现——将来换云端只加一个实现名 |

**接口刻意做成「按名创建」**（与 §9.1 的既有扩展点同一范式），
就是为了留住那条退路：如果 `faster-whisper` 的原生扩展在 PyInstaller 冻结形态下
收不进去（见 §7 P2 的排期前提），换成云端实现**不需要动任何调用方**。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AsrResult:
    """一次转写的结果。"""

    text: str
    provider: str
    language: str = ""
    duration_seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.text.strip()


class AsrError(RuntimeError):
    """转写失败（模型缺失 / 解码失败 / 依赖未装）。

    刻意与「转写成功但内容为空」区分：前者是**配置问题**（要把原因原样告诉用户，
    并说清怎么办），后者只是用户没说话。混成一个的话，
    用户看到「识别失败」会去反复重试，而真正的问题是模型没放对位置。
    """


class AsrProvider(ABC):
    """语音识别 provider。"""

    #: 实现名（与 `ASR_PROVIDER` 配置值一致）
    name: str = "none"

    @abstractmethod
    def available(self) -> bool:
        """当前是否可用（依赖已装、模型能找到）。"""

    @abstractmethod
    def transcribe(
        self, audio: bytes, *, language: str = "zh", filename: str = "audio.webm"
    ) -> AsrResult:
        """把音频字节转写成文本。

        抛出:
            AsrError: 依赖缺失 / 模型缺失 / 解码失败（`message` 要能直接展示给用户）。
        """

    def warmup(self) -> None:
        """预热（加载模型）。默认什么都不做；实现方按需覆盖。

        为什么需要：首次调用才加载模型会让用户第一次说话等十几秒——
        那不是「慢」，是「看起来坏了」。
        """
