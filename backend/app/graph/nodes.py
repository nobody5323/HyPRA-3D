"""LangGraph 节点实现（依赖注入式，便于单测与替换）。

节点职责（对应设计参照①的完整装配顺序）：
    load_persona → worldbook_recall → memory_recall
        → assemble_prompt → generate_reply → write_memory

各节点只做一件事，且都从 state 读取所需输入、返回增量字段；
外部依赖（人设/世界书/记忆/LLM/组装器）由构造参数注入。
"""

from app.llm.base import ChatMessage, LLMProvider
from app.llm.profiles import ModelProfile, ResolvedSampling, resolve_sampling
from app.memory.store import MemoryStore
from app.prompts.assemble import assemble_worldbook_section
from app.prompts.persona.loader import PersonaPreset
from app.prompts.renderer import render_persona_prompt
from app.prompts.sanitize import sanitize_reply
from app.prompts.style.loader import check_persona_compatibility
from app.prompts.style.models import StylePreset
from app.rag.prompt_manager import PromptManager
from app.graph.state import ChatState
from app.tools.emotion import (
    EMOTION_TOOL_NAME,
    build_emotion_tool,
    extract_emotion_fallback,
    parse_emotion_result,
)
from app.tools.builtin_tools import build_tool_context
from app.tools.registry import ToolRegistry
from app.worldbook.matcher import match_entries
from app.worldbook.models import WorldBookEntry


class ChatNodes:
    """一轮对话的全部节点（持有外部依赖）。"""

    def __init__(
        self,
        *,
        presets: dict[str, PersonaPreset],
        entries: list[WorldBookEntry],
        memory_store: MemoryStore,
        llm_provider: LLMProvider,
        prompt_manager: PromptManager | None = None,
        worldbook_budget: int = 400,
        styles: dict[str, StylePreset] | None = None,
        default_style_id: str = "modern-conversational",
        model_name: str = "",
        tool_registry: ToolRegistry | None = None,
        mood_store=None,
        max_tool_rounds: int = 2,
        profiles: list[ModelProfile] | None = None,
    ) -> None:
        self.presets = presets
        self.entries = entries
        self.memory = memory_store
        self.llm = llm_provider
        self.prompt_manager = prompt_manager or PromptManager()
        self.worldbook_budget = worldbook_budget
        # 文风预设（与 persona 正交）：未注入时风格层为空（下游可选）
        self.styles = styles or {}
        self.default_style_id = default_style_id
        self.model_name = model_name or getattr(llm_provider, "model", "")
        # Agent 行动层：工具注册表 + 情绪日记库
        self.tool_registry = tool_registry
        self.mood_store = mood_store
        self.max_tool_rounds = max_tool_rounds
        # 模型预设档：进程内缓存（传入 None 时由 resolve_sampling 自行读盘）
        self.profiles = profiles

    # ---------- 工具 ----------

    @staticmethod
    def _merge_warnings(state: ChatState, new: list[str]) -> list[str]:
        return [*state.get("warnings", []), *new]

    # ---------- ① 人设渲染 ----------

    def load_persona(self, state: ChatState) -> dict:
        """渲染角色人设（状态变量替换）。"""
        persona = self.presets[state["persona_id"]]
        context_vars = {"user_name": state.get("user_name", "朋友"), **state.get("state_vars", {})}
        rendered = render_persona_prompt(persona, context_vars)
        return {
            "persona_text": rendered.text,
            "warnings": self._merge_warnings(state, list(rendered.warnings)),
        }

    # ---------- ② 世界书命中 ----------

    def worldbook_recall(self, state: ChatState) -> dict:
        """关键词/正则触发 + 注入编排（priority + 预算）。"""
        hits = match_entries(self.entries, state.get("user_input", ""))
        text, skipped = assemble_worldbook_section(hits, self.worldbook_budget)
        return {
            "worldbook_hits": hits,
            "worldbook_skipped": skipped,
            "worldbook_text": text,
        }

    # ---------- ③ 三层记忆召回 ----------
    def memory_recall(self, state: ChatState) -> dict:
        """温层语义召回 + 冷层事实 + 摘要（按预判情绪加权，参照⑤）。

        时序说明：召回发生在生成之前，故用正则快速预判本轮情绪作为加权依据
        （生成后的精确情绪用于下一轮与记忆写入）。
        """
        pre_emotion = extract_emotion_fallback(state.get("user_input", ""))
        emotion_key = (
            pre_emotion.emotion.value
            if pre_emotion.emotion.value != "neutral"
            else None
        )
        ctx = self.memory.recall(
            state.get("companion_id", state["persona_id"]),
            state.get("user_input", ""),
            emotion=emotion_key,
        )
        return {
            "memory_context": ctx,
            "warm_lines": [r.record.text for r in ctx.memories],
            "fact_lines": [f.summary_text for f in ctx.facts],
            "summary_text": ctx.summary.content if ctx.summary else "",
            "warnings": self._merge_warnings(state, list(ctx.warnings)),
        }

    # ---------- ④ 分层组装 ----------

    def _resolve_style(self, state: ChatState) -> tuple[StylePreset | None, list[str]]:
        """解析本轮风格预设（state.style_id 优先，缺省用默认档），并做一致性校验。"""
        if not self.styles:
            return None, []
        style_id = state.get("style_id") or self.default_style_id
        style = self.styles.get(style_id)
        if style is None:
            return None, [f"未知风格预设「{style_id}」，已跳过风格层"]
        persona = self.presets.get(state["persona_id"])
        warnings = check_persona_compatibility(persona, style) if persona else []
        return style, warnings

    @staticmethod
    def _compose_style_text(style: StylePreset, style_hint: str) -> str:
        """风格指令块 = 文风预设指令 + 模型适配档的额外约束。"""
        parts = [style.instruction_block]
        if style_hint:
            parts.append(style_hint)
        return "\n\n".join(parts)

    def assemble_prompt(self, state: ChatState) -> dict:
        """用 PromptManager 按固定顺序与预算组装完整提示（含风格层与示例对话）。"""
        style, style_warnings = self._resolve_style(state)
        style_text = ""
        examples: list[tuple[str, str]] = []

        # 模型预设档总要解析：采样参数与推理开关都来自它，
        # 文风预设的 sampling 仅在其之上覆盖（因此 style 缺失时也不能跳过）。
        sampling: ResolvedSampling = resolve_sampling(
            self.model_name,
            style.sampling if style is not None else None,
            profiles=self.profiles,
            preset_id=state.get("preset_id") or None,
        )

        if style is not None:
            style_text = self._compose_style_text(style, sampling.style_hint)
            examples = [(e.user, e.assistant) for e in style.examples]

        built = self.prompt_manager.build(
            persona_text=state.get("persona_text", ""),
            user_input=state.get("user_input", ""),
            worldbook_text=state.get("worldbook_text", ""),
            warm_lines=state.get("warm_lines", []),
            fact_lines=state.get("fact_lines", []),
            summary_text=state.get("summary_text", ""),
            history=state.get("history", []),
            style_text=style_text,
            examples=examples,
        )
        return {
            "system_prompt": built.system_prompt,
            "messages": built.messages,
            "sampling": sampling,
            "style_id": style.id if style else "",
            "example_count": built.example_count,
            "warnings": self._merge_warnings(state, [*style_warnings, *built.warnings]),
        }

    # ---------- ⑤ LLM 生成 ----------

    def _build_tool_executor(self, state: ChatState):
        """构造工具执行器（绑定本轮会话上下文与依赖）。"""
        registry = self.tool_registry
        context = build_tool_context(
            state.get("companion_id", state["persona_id"]),
            session_id=state.get("session_id", ""),
            user_name=state.get("user_name", "用户"),
            mood_store=self.mood_store,
            memory_store=self.memory,
        )

        def _execute(name: str, arguments: str) -> str:
            if registry is None:
                return "当前没有可用工具。"
            return registry.execute(name, arguments, context).content

        return _execute

    def generate_reply(self, state: ChatState) -> dict:
        """生成回复 + 情绪识别 + Agent 工具调用。

        ① Agent 循环（chat_with_tool_loop）：模型可多轮调用工具（查记忆/记录情绪/呼吸引导），
           直到调用终止工具（情绪工具，携带最终回复）或给出纯文本回复；
        ② 降级：模型不支持工具调用 / 解析失败 → 普通 chat() + 正则兜底情绪。
        两通道都保证给出非空回复与一个情绪结果，绝不空转。
        """
        messages = [ChatMessage(**m) for m in state.get("messages", [])]
        sampling: ResolvedSampling | None = state.get("sampling")
        kwargs = sampling.to_provider_kwargs() if sampling is not None else {}
        # 推理开关：仅当 provider 声明支持时才传（避免破坏自定义/第三方实现）
        if (
            sampling is not None
            and sampling.enable_thinking is not None
            and getattr(self.llm, "supports_thinking_override", False)
        ):
            kwargs["enable_thinking"] = sampling.enable_thinking

        # 工具集：情绪工具（终止工具）+ Agent 行动层工具
        tools = [build_emotion_tool()]
        if self.tool_registry is not None and len(self.tool_registry) > 0:
            tools.extend(self.tool_registry.schemas())

        agent = self.llm.chat_with_tool_loop(
            messages,
            tools,
            self._build_tool_executor(state),
            final_tool=EMOTION_TOOL_NAME,
            max_rounds=self.max_tool_rounds,
            **kwargs,
        )
        tools_used = list(agent.tool_calls or [])

        # ① 终止工具（情绪）返回 → 解析出回复与情绪
        if agent.emotion_call:
            result = parse_emotion_result(agent.emotion_call)
            if result is not None:
                # 兜底清理：剔除 markdown/emoji 与模型的元评论（自我纠正）
                result.reply = sanitize_reply(result.reply)
                return {"reply": result.reply, "emotion": result, "tools_used": tools_used}

        # ② 模型只给了纯文本、未调用情绪工具 → **强制补一次结构化输出**。
        #    否则会直接落到关键词兜底：未命中关键词时情绪恒为 neutral / intensity 0.5
        #    （实测缺陷：前端情绪强度「始终 50%」）。靠提示词无法保证模型一定调工具，
        #    故此处用 tool_choice 强制（仅多一次调用，且只在模型未调工具时发生）。
        if agent.reply:
            # 末尾需要一条**指令消息**：实测仅以 assistant 结尾时，部分 OpenAI 兼容实现
            # 不会返回 tool_call（3 次中 2 次落回关键词兜底）。
            # 指令里保留用户原话，避免「最后一条用户消息」丢失真实输入
            # （兜底实现与部分模型会据此判情绪）。
            forced_calls = self.llm.chat_with_tools(
                [
                    *messages,
                    ChatMessage(role="assistant", content=agent.reply),
                    ChatMessage(
                        role="user",
                        content=(
                            f"{state.get('user_input', '')}\n\n"
                            "请调用工具，返回「我此刻的情绪判定」与「你刚才的回复」的完整文本。"
                        ),
                    ),
                ],
                [build_emotion_tool()],
                tool_choice={
                    "type": "function",
                    "function": {"name": EMOTION_TOOL_NAME},
                },
                **kwargs,
            )
            forced = next(
                (call for call in (forced_calls or []) if call.name == EMOTION_TOOL_NAME),
                None,
            )
            if forced is not None:
                result = parse_emotion_result(forced.arguments)
                if result is not None:
                    # 回复沿用首次生成的内容：避免风格漂移，也不多花一次生成
                    result.reply = sanitize_reply(agent.reply or result.reply)
                    return {
                        "reply": result.reply,
                        "emotion": result,
                        "tools_used": tools_used,
                    }

        # ③ 终极降级：普通回复 + 关键词兜底情绪（用真实回复替换占位文本）
        reply = sanitize_reply(agent.reply or self.llm.chat(messages, **kwargs))
        fallback = extract_emotion_fallback(state.get("user_input", ""))
        fallback.reply = reply
        return {"reply": reply, "emotion": fallback, "tools_used": tools_used}

    # ---------- ⑥ 回复后事件驱动写入 ----------

    def write_memory(self, state: ChatState) -> dict:
        """抽取事实 → 向量入库 → 摘要增量并入（参照③④）。

        本轮情绪作为 emotion_tag 随事实与向量一同落库，供后续加权召回。
        """
        emotion = state.get("emotion")
        writes = self.memory.remember_turn(
            state.get("companion_id", state["persona_id"]),
            state.get("user_input", ""),
            state.get("reply", ""),
            turn_index=state.get("turn_index", 0),
            source=state.get("session_id", ""),
            subject=state.get("user_name", "用户"),
            emotion=emotion.emotion.value if emotion is not None else None,
        )
        return {"writes": writes}
