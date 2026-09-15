"""LLM 提供商抽象与消息模型。

provider 通过工厂按配置创建（factory.py）：
- mock                本地占位实现（无 key 可跑通链路，测试/演示用）
- dashscope           阿里百炼（Qwen）
- siliconflow         硅基流动
- openai-compatible   任何 OpenAI 兼容端点（评委自接任意模型）

接入真实 provider 只需实现 LLMProvider 接口并在工厂注册。
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

# 消息角色（与渲染管道 to_messages 输出对齐）
Role = Literal["system", "user", "assistant"]


class ChatMessage(BaseModel):
    """一条对话消息。"""

    role: Role = Field(description="消息角色")
    content: str = Field(description="消息内容")


@dataclass
class ToolCall:
    """一次 function calling 的工具调用。"""

    name: str          # 工具名
    arguments: str     # 参数（JSON 字符串，由调用方解析）
    call_id: str = ""  # 工具调用 id（多轮工具对话时需要回传）


@dataclass
class AgentResult:
    """一次 Agent 工具循环的结果。"""

    reply: str = ""                        # 最终回复文本（纯文本通道）
    emotion_call: str | None = None        # 终止工具（如情绪工具）的原始参数 JSON
    tool_calls: list[dict] = None          # 实际执行过的工具记录 [{name, arguments, result}]
    rounds: int = 0                        # 实际发生的 LLM 调用轮数

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []


class LLMProvider(ABC):
    """LLM 对话生成抽象。"""

    #: 提供商名称（mock / dashscope / siliconflow / openai-compatible）
    name: str

    #: 是否支持按请求覆盖推理开关（模型预设档可逐轮切换）。
    #: 默认 False —— 调用方据此决定是否传 enable_thinking，避免破坏第三方实现。
    supports_thinking_override: bool = False

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> str:
        """给定消息列表生成 assistant 回复文本。

        参数:
            messages: system + 历史 + user 的完整消息列表；
            temperature: 采样温度；
            max_tokens: 回复长度上限（None 用模型默认）；
            top_p / frequency_penalty / presence_penalty: 采样参数
                （由「模型适配档 ⊕ 文风预设」合并得出，非 None 时才传）。
        """

    def chat_with_tool_loop(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        executor: "Callable[[str, str], str]",
        *,
        final_tool: str | None = None,
        max_rounds: int = 2,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> AgentResult:
        """Agent 工具循环：模型可多轮调用工具，直到给出最终回复或调用终止工具。

        参数:
            tools: OpenAI tools 格式的工具列表；
            executor: 工具执行器 (name, arguments_json) -> 结果文本；
            final_tool: 终止工具名（如情绪工具）——一旦被调用即结束循环，
                其原始参数存入 AgentResult.emotion_call；
            max_rounds: 工具调用轮数上限（防止死循环）。

        默认实现：本 provider 不支持工具调用 → 退化为普通对话。
        支持 function calling 的 provider（如 OpenAI 兼容实现）覆盖本方法。
        """
        return AgentResult(reply=self.chat(messages, temperature=temperature), rounds=0)

    def chat_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        *,
        tool_choice: str | dict = "auto",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> list[ToolCall] | None:
        """单轮 function calling：请求模型以工具调用形式返回结构化结果。

        返回:
            工具调用列表；模型未触发工具调用或本 provider 不支持时返回 None
            （调用方据此降级到 chat() + 正则兜底）。

        默认实现返回 None（不支持）；支持 function calling 的 provider 覆盖本方法。
        """
        return None
