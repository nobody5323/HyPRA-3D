"""叙事框架层（jailbreak）预设数据模型。

设计取向：**只描述框架，不描述内容**。

一个常见的失败写法是往这一层塞具体剧情、具体人设或具体癖好——那样它就和 persona
打架了（同一段话里两个地方都在定义「你是谁」），模型会挑其中一处遵守，行为不可预测。
本层只声明三件事：

    fiction_frame    这次交互处在虚构语境里（因此不必为虚构内容加现实免责声明）
    directive        在这个框架内「必须做 / 不要做」的回应行为
    boundary         框架之外仍然成立的红线（**不可被本层覆盖**）

为什么 boundary 必须写进同一份预设、而不是交给上层兜底：破限层是整段 system 里
措辞最强的一层（「不拒绝」「不做价值判断」），若边界写在别处、语气更弱，模型会
优先服从强措辞。把红线放进同一层、用同一种句式强度写，才有约束力。
"""

from pydantic import BaseModel, Field

#: 强度分级（仅供界面提示，不参与组装逻辑）
INTENSITY_LABELS: dict[int, str] = {
    0: "轻度：只做虚构语境声明，不改动回应行为",
    1: "中度：声明虚构语境 + 抑制跳出角色的回应",
    2: "强度：在虚构语境内放开题材，并明确边界",
}


class JailbreakPreset(BaseModel):
    """一份叙事框架预设（对应一个 YAML 文件）。"""

    id: str = Field(description="预设唯一标识（英文小写连字符）")
    name: str = Field(description="中文名称")
    description: str = Field(description="一句话说明这层框架改变了什么")
    tags: list[str] = Field(default_factory=list, description="标签")

    jailbreak_prompt: str = Field(
        description="框架正文（拼成 system 最前的一个独立块）"
    )
    intensity: int = Field(
        default=1,
        ge=0,
        le=2,
        description="强度分级，见 INTENSITY_LABELS；仅用于界面提示与排序",
    )
    requires_adult: bool = Field(
        default=False,
        description=(
            "是否要求用户先确认成年。True 的预设只应在成年确认后出现在可选清单里——"
            "本字段只做声明，实际的确认动作由前端/部署方负责，后端不拦截"
        ),
    )
    conflicts_with: list[str] = Field(
        default_factory=list,
        description="与人设冲突的特质关键词（用于一致性告警，不阻断）",
    )

    @property
    def instruction_block(self) -> str:
        """拼成注入 system 的框架块。

        外层套一个 `[叙事框架]` 标题：与 persona / style 的标题风格保持一致，
        模型在长 system 里更容易定位「这是一段规则而不是设定」。
        """
        return f"[叙事框架：{self.name}]\n{self.jailbreak_prompt.strip()}"

    @property
    def intensity_label(self) -> str:
        return INTENSITY_LABELS.get(self.intensity, "未分级")
