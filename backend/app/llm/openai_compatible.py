"""OpenAI 兼容 LLM provider（基于 openai 官方 SDK）。

覆盖国内主流托管 API（均提供 OpenAI 兼容端点）：
- dashscope     阿里百炼（Qwen 系列）
- siliconflow   硅基流动
- openai-compatible  任意兼容端点（评审自接其他模型）

设计要点：
- 依赖注入 http_client，便于用 httpx.MockTransport 做**无网络测试**；
- base_url 未显式提供时按 provider 名取默认值；
- 真实 key 一律从 backend/.env 读取，不入库。
"""

from openai import OpenAI

from app.llm.base import ChatMessage, LLMProvider, ToolCall

# 各提供商默认 base_url（可被 .env 的 LLM_BASE_URL 覆盖）
DEFAULT_BASE_URLS: dict[str, str] = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


class OpenAICompatibleProvider(LLMProvider):
    """通过 openai SDK 调用任意 OpenAI 兼容端点。"""

    #: 支持按请求覆盖推理开关（模型预设档可逐轮切换）
    supports_thinking_override = True

    def __init__(
        self,
        *,
        name: str = "openai-compatible",
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        enable_thinking: bool | None = None,
        http_client=None,
    ) -> None:
        """初始化。

        参数:
            name: 提供商名（dashscope / siliconflow / openai-compatible）；
            api_key: 云端 key（来自 .env）；
            model: 模型名，如 qwen2.5-7b-instruct；
            base_url: 端点地址，缺省按 name 取默认值；
            timeout / max_retries: 网络超时与重试（SDK 自带退避重试）；
            http_client: 自定义 httpx 客户端（测试注入用）。
        """
        resolved_base = base_url or DEFAULT_BASE_URLS.get(name, "")
        if not resolved_base:
            raise ValueError(
                f"provider「{name}」需要显式 base_url（请设置 LLM_BASE_URL）"
            )
        if not api_key:
            raise ValueError(f"provider「{name}」缺少 api_key（请设置 LLM_API_KEY）")

        self.name = name
        self.model = model
        # None = 不传该参数（兼容非推理模型）；False = 关闭思考（推理模型提速）
        self._enable_thinking = enable_thinking
        self._client = OpenAI(
            api_key=api_key,
            base_url=resolved_base,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
        )

    def list_models(self) -> list[str]:
        """列出端点可用的模型名（OpenAI 兼容端点通常提供 `GET /models`）。

        去重并排序：部分服务商会在列表里重复返回同一模型 ID。
        """
        response = self._client.models.list()
        return sorted({item.id for item in response.data if getattr(item, "id", "")})

    def _extra_body(self, enable_thinking: bool | None = None) -> dict | None:
        """非标准参数（如推理模型的思考开关）。None 时不传，避免影响普通模型。

        参数 enable_thinking 优先于构造时的实例默认值——供模型预设档逐轮指定。
        """
        effective = self._enable_thinking if enable_thinking is None else enable_thinking
        if effective is None:
            return None
        return {"enable_thinking": effective}

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
        """调用 chat/completions，返回助手回复文本。"""
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        # 仅传非 None 的采样参数，避免污染请求（部分端点不接受 None）
        for key, value in (
            ("max_tokens", max_tokens),
            ("top_p", top_p),
            ("frequency_penalty", frequency_penalty),
            ("presence_penalty", presence_penalty),
        ):
            if value is not None:
                kwargs[key] = value
        extra_body = self._extra_body(enable_thinking)
        if extra_body is not None:
            kwargs["extra_body"] = extra_body

        response = self._client.chat.completions.create(**kwargs)
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

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
    ):
        """Agent 工具循环（OpenAI tools 协议）。

        流程：LLM(tools) → 若有 tool_calls → 执行并回传 → 再调 LLM，
        直到模型给出纯文本回复、或调用终止工具（final_tool）、或轮数用尽。
        """
        from app.llm.base import AgentResult

        api_messages: list[dict] = [
            {"role": m.role, "content": m.content} for m in messages
        ]
        executed: list[dict] = []
        rounds = 0

        def _sampling_kwargs() -> dict:
            kwargs: dict = {"model": self.model, "temperature": temperature}
            for key, value in (
                ("max_tokens", max_tokens),
                ("top_p", top_p),
                ("frequency_penalty", frequency_penalty),
                ("presence_penalty", presence_penalty),
            ):
                if value is not None:
                    kwargs[key] = value
            extra_body = self._extra_body(enable_thinking)
            if extra_body is not None:
                kwargs["extra_body"] = extra_body
            return kwargs

        for round_index in range(max_rounds):
            rounds = round_index + 1
            response = self._client.chat.completions.create(
                messages=api_messages, tools=tools, tool_choice="auto", **_sampling_kwargs()
            )
            if not response.choices:
                break
            message = response.choices[0].message
            tool_calls = list(message.tool_calls or [])

            # ① 无工具调用 → 最终纯文本回复
            if not tool_calls:
                return AgentResult(
                    reply=message.content or "", tool_calls=executed, rounds=rounds
                )

            # ② 终止工具被调用 → 结束循环；但同轮的**业务工具仍需执行**
            #    （模型可能在同一轮同时「办事」与给出回复，不应丢失办事意图）
            final_call = next(
                (c for c in tool_calls if final_tool and c.function.name == final_tool),
                None,
            )
            if final_call is not None:
                for call in tool_calls:
                    if call.function.name == final_tool:
                        continue
                    content = executor(call.function.name, call.function.arguments)
                    executed.append(
                        {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                            "result": content,
                        }
                    )
                return AgentResult(
                    emotion_call=final_call.function.arguments,
                    tool_calls=executed,
                    rounds=rounds,
                )

            # ③ 执行工具并把结果回传（需按协议补 assistant + tool 两条消息）
            api_messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.function.name,
                                "arguments": c.function.arguments,
                            },
                        }
                        for c in tool_calls
                    ],
                }
            )
            for call in tool_calls:
                content = executor(call.function.name, call.function.arguments)
                executed.append(
                    {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                        "result": content,
                    }
                )
                api_messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": content}
                )

        # 收尾轮：不再执行工具，只取最终结果（终止工具优先，否则纯文本）
        rounds += 1
        response = self._client.chat.completions.create(
            messages=api_messages, tools=tools, tool_choice="auto", **_sampling_kwargs()
        )
        if not response.choices:
            return AgentResult(reply="", tool_calls=executed, rounds=rounds)
        message = response.choices[0].message
        final_call = next(
            (c for c in (message.tool_calls or []) if final_tool and c.function.name == final_tool),
            None,
        )
        if final_call is not None:
            return AgentResult(
                emotion_call=final_call.function.arguments,
                tool_calls=executed,
                rounds=rounds,
            )
        return AgentResult(
            reply=message.content or "", tool_calls=executed, rounds=rounds
        )

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
        """function calling（OpenAI tools 协议）。

        模型未触发工具调用（如老模型不支持）时返回 None，由调用方降级。
        """
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": temperature,
        }
        for key, value in (
            ("max_tokens", max_tokens),
            ("top_p", top_p),
            ("frequency_penalty", frequency_penalty),
            ("presence_penalty", presence_penalty),
        ):
            if value is not None:
                kwargs[key] = value
        extra_body = self._extra_body(enable_thinking)
        if extra_body is not None:
            kwargs["extra_body"] = extra_body

        response = self._client.chat.completions.create(**kwargs)
        if not response.choices:
            return None
        message = response.choices[0].message
        if not message.tool_calls:
            return None
        return [
            ToolCall(
                name=call.function.name,
                arguments=call.function.arguments,
                call_id=getattr(call, "id", "") or "",
            )
            for call in message.tool_calls
        ]
