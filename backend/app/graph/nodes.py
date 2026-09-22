"""LangGraph 节点实现（依赖注入式，便于单测与替换）。

节点职责（对应设计参照①的完整装配顺序）：
    load_persona → worldbook_recall → memory_recall
        → assemble_prompt → generate_reply → write_memory

各节点只做一件事，且都从 state 读取所需输入、返回增量字段；
外部依赖（人设/世界书/记忆/LLM/组装器）由构造参数注入。
"""

from app.llm.base import ChatMessage, LLMProvider
from app.llm.profiles import (
    ModelProfile,
    ResolvedSampling,
    resolve_profile,
    resolve_sampling,
)
from app.memory.knowledge.retriever import KnowledgeRetriever
from app.memory.store import MemoryStore
from app.memory.warm.embedding import EmbeddingProvider
from app.prompts.assemble import assemble_worldbook_section
from app.prompts.persona.loader import PersonaPreset
from app.prompts.renderer import render_persona_prompt
from app.prompts.sanitize import sanitize_reply
from app.prompts.st_compat import (
    MARKER_WORLD_INFO_AFTER,
    MARKER_WORLD_INFO_BEFORE,
    ParsedPreset,
    STRenderContext,
    render_st_preset,
)
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
from app.worldbook.vector_index import WorldBookVectorIndex

#: 能真正透传给 provider 的采样参数。
#: ST 的 top_k / top_a / min_p / repetition_penalty / seed / n 只保存与展示，
#: 不透传（国内托管 API 多会拒绝未知参数），界面需标注“当前模型忽略”（契约 §4）。
_APPLICABLE_SAMPLING = (
    "temperature",
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "max_tokens",
)


class ChatNodes:
    """一轮对话的全部节点（持有外部依赖）。"""

    def __init__(
        self,
        *,
        presets: dict[str, PersonaPreset],
        entries: list[WorldBookEntry],
        memory_store: MemoryStore,
        knowledge: KnowledgeRetriever | None = None,
        embedding_provider: EmbeddingProvider | None = None,
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
        # 个人记忆检索器：未注入时该层为空（功能可关，不阻断对话）
        self.knowledge = knowledge
        # 世界书语义向量索引：构造时一次性编码全部条目；provider 为 None 时
        # 索引为空，向量通道静默关闭（关键词/正则通道不受影响）。
        self.worldbook_index = WorldBookVectorIndex(entries, embedding_provider)
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
        """关键词 / 正则 / 语义向量三通道触发 + 注入编排（priority + 预算）。"""
        hits = match_entries(
            self.entries,
            state.get("user_input", ""),
            vector_index=self.worldbook_index,
        )
        text, skipped = assemble_worldbook_section(hits, self.worldbook_budget)
        return {
            "worldbook_hits": hits,
            "worldbook_skipped": skipped,
            "worldbook_text": text,
        }

    # ---------- ②′ 个人记忆检索 ----------

    def knowledge_recall(self, state: ChatState) -> dict:
        """个人记忆检索（混合检索：BM25 + 稠密向量 → RRF）。

        与情景记忆共用检索底座，但**不做时间衰减、不做情绪加权**——
        知识不老化，且是客观的。这层是「用户给过我的资料」，
        与「我们一起经历过什么」（情景记忆）职责不同。
        """
        if self.knowledge is None:
            return {"knowledge_lines": []}

        companion_id = state.get("companion_id", state.get("persona_id", ""))
        try:
            hits = self.knowledge.retrieve(
                companion_id, state.get("user_input", "")
            )
        except Exception as exc:  # noqa: BLE001 - 知识库不可用不应阻断对话
            return {
                "knowledge_lines": [],
                "warnings": self._merge_warnings(
                    state, [f"知识库检索失败（已降级）：{exc}"]
                ),
            }
        return {"knowledge_lines": [hit.chunk.text for hit in hits]}

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
            "warnings": self._merge_warnings(state, list(ctx.warnings)),
        }

    # ---------- ④ 分层组装 ----------

    def _resolve_style(self, state: ChatState) -> tuple[StylePreset | None, list[str]]:
        """解析本轮风格预设（state.style_id 优先，缺省用默认档），并做一致性校验。

        `style_id` 取 `"none"` 表示**显式关闭文风层**：文风预设的 sampling 优先级最高，
        会覆盖预设作者的采样意图；酒馆用户的“纯预设体验”需要能关掉它。
        """
        style_id = state.get("style_id") or self.default_style_id
        if style_id == "none" or not self.styles:
            return None, []
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
        """组装完整提示（两条路径，契约文档 §12）。

        - 本轮选定了导入的 ST 预设 → `_assemble_with_st_preset`（复现 ST 组装语义）；
        - 否则走内置分层路径 → `_assemble_builtin`（PromptManager 固定顺序 + 预算）。
        """
        st_preset: ParsedPreset | None = state.get("st_preset")
        if st_preset is not None:
            return self._assemble_with_st_preset(state, st_preset)
        return self._assemble_builtin(state)

    def _assemble_builtin(self, state: ChatState) -> dict:
        """内置分层组装：PromptManager 固定顺序 + 层内/总量预算。"""
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
            knowledge_lines=state.get("knowledge_lines", []),
            warm_lines=state.get("warm_lines", []),
            fact_lines=state.get("fact_lines", []),
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

    # ---------- ④′ ST 预设组装（导入的酒馆预设）----------

    def _assemble_with_st_preset(self, state: ChatState, st_preset: ParsedPreset) -> dict:
        """按导入的 ST 预设组装 messages（契约文档 §5–§7）。

        与内置路径的差异：
        - 消息顺序由预设的 `prompt_order` 决定（含 In-Chat 深度注入）；
        - HyPRA 的记忆层不再占固定分层，而是作为**扩展注入**参与同层合并（§7）；
        - 文风层追加到 jailbreak 槽位之后，不覆盖预设正文（§6.6）；
        - 预设正文里的 ST 宏按会话解析（§11）。
        """
        style, style_warnings = self._resolve_style(state)
        style_text = ""
        examples: list[tuple[str, str]] = []

        sampling = self._resolve_sampling_with_st(state, style, st_preset)

        if style is not None:
            style_text = self._compose_style_text(style, sampling.style_hint)
            examples = [(e.user, e.assistant) for e in style.examples]

        persona = self.presets.get(state["persona_id"])
        worldbook_before, worldbook_after, worldbook_warnings = self._split_worldbook(
            state, st_preset
        )

        context = STRenderContext(
            persona_text=state.get("persona_text", ""),
            persona_personality=self._persona_personality(persona),
            worldbook_before=worldbook_before,
            worldbook_after=worldbook_after,
            history=list(state.get("history", [])),
            examples=examples,
            user_input=state.get("user_input", ""),
            memory_text=self._compose_memory_text(state),
            style_text=style_text,
            persona_name=persona.name if persona is not None else "",
            user_name=state.get("user_name", ""),
            macro_variables=dict(state.get("macro_variables", {})),
        )
        rendered = render_st_preset(st_preset, context)

        return {
            "system_prompt": rendered.system_prompt,
            "messages": rendered.messages,
            "sampling": sampling,
            "style_id": style.id if style else "",
            "example_count": rendered.example_count,
            # 宏变量回传：{{setvar::}} 的结果由路由层落库（按会话隔离）
            "macro_variables": dict(context.macro_variables),
            "st_preset_meta": {
                "source_format": st_preset.source_format,
                "used_markers": rendered.used_markers,
                "empty_markers": rendered.empty_markers,
                "unresolved_macros": rendered.unresolved_macros,
                "in_chat_count": rendered.in_chat_count,
            },
            "warnings": self._merge_warnings(
                state, [*style_warnings, *worldbook_warnings, *rendered.warnings]
            ),
        }

    def _resolve_sampling_with_st(
        self,
        state: ChatState,
        style: StylePreset | None,
        st_preset: ParsedPreset,
    ) -> ResolvedSampling:
        """三级采样合并（契约文档 §10）：内置档 → ST 预设 → 文风预设。

        不能直接用 `resolve_sampling`：它只支持「内置 ⊕ 文风」两级，
        会把文风参数压在 ST 预设之前，与约定的优先级不符。
        """
        profile = resolve_profile(
            self.model_name, self.profiles, state.get("preset_id") or None
        )
        merged: dict = {}
        sources: dict[str, str] = {}

        def _apply(values: dict, source: str) -> None:
            for key, value in values.items():
                if value is not None:
                    merged[key] = value
                    sources[key] = source

        _apply(profile.sampling, profile.id)
        _apply(
            {
                key: value
                for key, value in st_preset.preset.sampling.items()
                if key in _APPLICABLE_SAMPLING
            },
            f"st:{state.get('st_preset_id', '')}",
        )
        _apply(style.sampling if style is not None else {}, "style")

        # 思考开关：内置档打底；ST 预设给了 show_thoughts 时以它为准
        thinking = st_preset.preset.enable_thinking
        if thinking is None:
            thinking = profile.enable_thinking

        return ResolvedSampling(
            temperature=float(merged.get("temperature", 0.8)),
            max_tokens=int(merged["max_tokens"]) if merged.get("max_tokens") else None,
            top_p=merged.get("top_p"),
            frequency_penalty=merged.get("frequency_penalty"),
            presence_penalty=merged.get("presence_penalty"),
            style_hint=profile.style_hint.strip(),
            profile_id=profile.id,
            profile_label=profile.display_name,
            enable_thinking=thinking,
            sources=sources,
        )

    @staticmethod
    def _persona_personality(persona: PersonaPreset | None) -> str:
        """人设的「性格」段：本项目 persona 没有独立字段，用定位 + 标签近似。"""
        if persona is None:
            return ""
        parts = [persona.title, "、".join(persona.tags)]
        return "；".join(part for part in parts if part)

    @staticmethod
    def _compose_memory_text(state: ChatState) -> str:
        """把 HyPRA 的记忆层拼成一块文本，作为 ST 扩展注入的内容（契约文档 §7）。

        分节口径与内置路径的 PromptManager 保持一致（`[参考资料]` / `[记忆回忆]`），
        避免同一份记忆在两条路径下呈现不同结构。
        """
        sections: list[str] = []

        knowledge = [line for line in state.get("knowledge_lines", []) if line.strip()]
        if knowledge:
            sections.append("[参考资料]\n" + "\n".join(f"- {line}" for line in knowledge))

        memory_parts: list[str] = []
        warm = [line for line in state.get("warm_lines", []) if line.strip()]
        if warm:
            memory_parts.append("相关回忆：\n" + "\n".join(f"- {line}" for line in warm))
        facts = [line for line in state.get("fact_lines", []) if line.strip()]
        if facts:
            memory_parts.append("已知事实：\n" + "\n".join(f"- {line}" for line in facts))
        if memory_parts:
            sections.append("[记忆回忆]\n" + "\n".join(memory_parts))

        return "\n\n".join(sections)

    @staticmethod
    def _split_worldbook(
        state: ChatState, st_preset: ParsedPreset
    ) -> tuple[str, str, list[str]]:
        """世界书内容注入哪个 marker 槽位（返回 before/after 与告警）。

        本项目只有一个世界书块，而 ST 分为 before/after 两个槽位，故择一注入：
        优先 `worldInfoBefore`（ST 默认顺序里它承载情境补充）；该条目未启用时
        改投 `worldInfoAfter`；两者都未启用则告警——否则世界书会被静默丢掉。
        """
        text = state.get("worldbook_text", "")
        if not text.strip():
            return "", "", []

        enabled = {entry.identifier for entry in st_preset.order if entry.enabled}
        if MARKER_WORLD_INFO_BEFORE in enabled:
            return text, "", []
        if MARKER_WORLD_INFO_AFTER in enabled:
            return "", text, []
        return "", "", ["预设未启用世界书槽位，本轮世界书命中未注入"]

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
        """抽取新事实与状态变更指令 → 写入语义记忆；情景记忆同步入库（参照③）。

        本轮情绪作为 emotion_tag 随事实与向量一同落库，供后续加权召回。

        注意：本方法**已不再作为图节点**（图以 generate_reply 结尾）——
        写入由路由层后台调用，避免抽取延迟阻塞用户。保留此方法供显式调用与测试。
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
