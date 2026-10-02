"""视觉理解（VLM）：把用户分享的图片变成**中性事实描述**。

设计见 `docs/proactive-multimodal.md` §4.4。已决策：**只做「用户主动分享」**，
不做摄像头、不做连续屏幕感知（隐私成本远高于演示收益）。

## 已决策的模型：Qwen2.5-VL

| provider | 依赖 | 状态 |
| --- | --- | --- |
| `none` | — | **默认**。不渲染「发图」入口（不是「点了没反应」） |
| `openai-compatible` | 零新依赖（复用 openai SDK） | **本次采用**：Qwen2.5-VL（阿里百炼 `qwen2.5-vl-*` / 硅基流动） |

## 为什么视觉模型与对话模型**分开配**

`app/llm/profiles.yaml` 是**纯文本假设**（采样档、思考开关都围绕文本模型）。
视觉理解需要多模态模型，因此 `VISION_MODEL` 是独立配置项，与 `LLM_MODEL` 解耦。

由此得到一条重要的降级性质：**视觉模型缺失时，整个对话链路照常工作**。
「模型看图」和「模型对话」是两次调用、两个模型，互不干扰——
把两者绑在一起的话，没配 VL 模型就会连聊天都用不了。

## 为什么输出必须是「中性事实」

「这张图里有一个看起来很难过的人」是**推断**，而推断属于人设的职责
（它才知道这个角色会怎么解读）。感知层只该说「图中是一位坐在窗边的女性，
窗外在下雨」——把解读权留给模型，感知层只提供素材。
这条与 `context.py` 的「不写行动建议」是同一条纪律。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class VisionResult:
    """一次图片理解的结果。"""

    #: 中性事实描述（多行短句）
    description: str
    provider: str
    model: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.description.strip()


class VisionError(RuntimeError):
    """图片理解失败（依赖缺失 / 未配置 / 接口报错 / 图片过大）。

    与「描述为空」区分：前者是**配置或调用问题**（原因要原样告诉用户），
    后者只是模型没看出什么。
    """


class VisionProvider(ABC):
    """图片理解 provider。"""

    #: 实现名（与 `VISION_PROVIDER` 配置值一致）
    name: str = "none"

    @abstractmethod
    def available(self) -> bool:
        """当前是否可用（已配置 key 与模型）。"""

    @abstractmethod
    def describe(
        self, image: bytes, *, mime: str = "image/png", question: str = ""
    ) -> VisionResult:
        """把图片变成中性事实描述。

        参数:
            question: 用户随图附带的问题（可选）。为空时用默认的「客观描述」指令。

        抛出:
            VisionError: 未配置 / 调用失败（`message` 要能直接展示给用户）。
        """

    def warmup(self) -> None:
        """预热。默认不做——云端 VLM 没有可预热的本体（本地模型才需要）。"""
