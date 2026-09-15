"""Mock LLM：无 key 的占位实现（开发/测试/演示兜底）。

不调用任何外部服务，根据最后一条 user 消息做**关键词共情**，生成确定性的、
符合「苏澄」温柔基调的占位回复；同时支持 function calling 协议
（chat_with_tools），使情绪链路在无 key 环境下也能完整跑通。
真实 key 接入后，chat 流程无需改动即可切换 provider（见 factory.create_llm_provider）。

设计取舍：不把用户原文直接嵌进回复（读起来破碎），改用情绪/话题关键词
匹配 + 通用承接话术，输出更自然、更适合演示。
"""

import json

from app.llm.base import AgentResult, ChatMessage, LLMProvider, ToolCall

# 关键词 → 共情话术（按序匹配，首个命中生效）
_KEYWORD_REPLIES: list[tuple[tuple[str, ...], str]] = [
    (
        ("失眠", "睡不着", "熬夜", "做梦"),
        "听起来你最近休息得不太好，睡不着的夜晚总是格外难熬。"
        "我在这儿陪着，你想说说今晚在想什么吗？",
    ),
    (
        ("压力", "焦虑", "崩溃", "累", "疲惫", "撑不住"),
        "能感觉到这段时间你扛了挺多，辛苦了。"
        "不用急着解决什么，先在这儿喘口气也行。",
    ),
    (
        ("难过", "伤心", "哭", "委屈", "失落"),
        "嗯，我在听着。难过的时候不必急着好起来，"
        "我陪你待一会儿，慢慢说。",
    ),
    (
        ("妈妈", "爸爸", "家人", "父母", "孩子"),
        "家里的事总是牵动人心，你愿意的话，"
        "可以多说一些——你现在的感受，比事情本身更重要。",
    ),
    (
        ("工作", "老板", "辞职", "同事", "加班"),
        "工作带来的疲惫常常藏在心里最深处。"
        "你最近是在为哪件事耗神？我听着呢。",
    ),
    (
        ("谢谢", "感谢"),
        "不用客气，能陪你说说话，我也很安心。",
    ),
]

# 未命中任何关键词时的通用承接话术
_DEFAULT_REPLY = (
    "我在这儿听着呢。不用急着把话说清楚，"
    "想到哪儿说到哪儿就好——你现在最想聊的是什么？"
)


class MockLLMProvider(LLMProvider):
    """确定性占位实现（关键词共情，无网络请求）。

    scripted_rounds: 可选的「脚本」，用于测试多轮 Agent 工具循环——
    每个元素是某一轮返回的 tool_calls；脚本用尽后回落默认行为
    （直接返回情绪工具调用）。
    """

    name = "mock"

    #: 支持按请求覆盖推理开关（便于测试断言预设档已生效）
    supports_thinking_override = True

    def __init__(self, scripted_rounds: list[list[ToolCall]] | None = None) -> None:
        self._script: list[list[ToolCall]] = list(scripted_rounds or [])
        self._cursor = 0
        #: 最近一次请求携带的推理开关（None = 未传）——供测试断言
        self.last_enable_thinking: bool | None = None

    def reset_script(self) -> None:
        """重置脚本游标（同实例多次调用时用）。"""
        self._cursor = 0

    def _next_scripted_calls(
        self, messages: list[ChatMessage], enable_thinking: bool | None = None
    ) -> list[ToolCall]:
        """取本轮的 tool_calls：优先按脚本，脚本用尽则回落默认（情绪工具）。"""
        if self._cursor < len(self._script):
            calls = self._script[self._cursor]
            self._cursor += 1
            return calls
        return self.chat_with_tools(messages, [], enable_thinking=enable_thinking) or []

    def chat_with_tool_loop(
        self,
        messages: list[ChatMessage],
        tools: list[dict],
        executor,
        *,
        final_tool: str | None = None,
        max_rounds: int = 2,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        enable_thinking: bool | None = None,
    ) -> AgentResult:
        """模拟 Agent 循环：按脚本模拟「调用工具 → 回传 → 再调用」的过程。"""
        self.last_enable_thinking = enable_thinking
        executed: list[dict] = []
        rounds = 0
        for _ in range(max_rounds):
            rounds += 1
            calls = self._next_scripted_calls(messages, enable_thinking)
            if not calls:
                reply = self.chat(
                    messages, temperature=temperature, enable_thinking=enable_thinking
                )
                return AgentResult(reply=reply, tool_calls=executed, rounds=rounds)

            final_call = next(
                (c for c in calls if final_tool and c.name == final_tool), None
            )
            if final_call is not None:
                # 同轮的其它（业务）工具仍需执行，不丢失「办事」意图
                for call in calls:
                    if call.name == final_tool:
                        continue
                    content = executor(call.name, call.arguments)
                    executed.append(
                        {"name": call.name, "arguments": call.arguments, "result": content}
                    )
                return AgentResult(
                    emotion_call=final_call.arguments, tool_calls=executed, rounds=rounds
                )

            for call in calls:
                content = executor(call.name, call.arguments)
                executed.append(
                    {"name": call.name, "arguments": call.arguments, "result": content}
                )

        # 收尾轮：不再执行工具，只取最终结果（终止工具或纯文本）
        rounds += 1
        calls = self._next_scripted_calls(messages, enable_thinking)
        final_call = next(
            (c for c in calls if final_tool and c.name == final_tool), None
        )
        if final_call is not None:
            return AgentResult(
                emotion_call=final_call.arguments, tool_calls=executed, rounds=rounds
            )
        return AgentResult(
            reply=self.chat(messages, temperature=temperature),
            tool_calls=executed,
            rounds=rounds,
        )

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        enable_thinking: bool | None = None,
    ) -> str:
        self.last_enable_thinking = enable_thinking
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        for keywords, reply in _KEYWORD_REPLIES:
            if any(word in last_user for word in keywords):
                return reply
        return _DEFAULT_REPLY

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
        enable_thinking: bool | None = None,
    ) -> list[ToolCall] | None:
        """模拟 function calling：用关键词规则生成结构化结果（回复 + 情绪）。"""
        self.last_enable_thinking = enable_thinking
        from app.tools.emotion import EMOTION_TOOL_NAME, extract_emotion_fallback

        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        fallback = extract_emotion_fallback(last_user)
        arguments = json.dumps(
            {
                "reply": self.chat(messages, enable_thinking=enable_thinking),  # 复用共情话术
                "emotion": fallback.emotion.value,
                "intensity": fallback.intensity,
                "confidence": 0.5,
                "evidence": fallback.evidence,
            },
            ensure_ascii=False,
        )
        return [
            ToolCall(
                name=EMOTION_TOOL_NAME,
                arguments=arguments,
                call_id="mock-tool-call-1",
            )
        ]
