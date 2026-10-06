"""LangGraph 节点实现（依赖注入式，便于单测与替换）。

节点职责（对应设计参照①的完整装配顺序）：
    load_persona → worldbook_recall → memory_recall
        → assemble_prompt → generate_reply → write_memory

各节点只做一件事，且都从 state 读取所需输入、返回增量字段；
外部依赖（人设/世界书/记忆/LLM/组装器）由构造参数注入。
"""

from dataclasses import replace

from app.llm.base import ChatMessage, LLMProvider
from app.llm.profiles import (
    ModelProfile,
    ResolvedSampling,
    resolve_profile,
    resolve_sampling,
)
from app.llm.reasoning import ensure_reasoning_headroom, is_reasoning_model
from app.memory.knowledge.mounted import load_mounted_books
from app.memory.knowledge.retriever import KnowledgeRetriever
from app.memory.store import MemoryStore
from app.memory.warm.embedding import EmbeddingProvider
from app.prompts.assemble import insert_at_depth, place_worldbook
from app.prompts.jailbreak.loader import check_persona_compatibility as check_jailbreak_conflicts
from app.prompts.jailbreak.models import JailbreakPreset
from app.prompts.persona.loader import PersonaPreset
from app.prompts.renderer import render_persona_background, render_persona_prompt
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
from app.rag.prompt_manager import DIALOGUE_ONLY_RULE, PERCEPTION_SECTION, PromptManager
from app.session.mode import MODE_TAVERN, normalize_mode
from app.skills.registry import get_skill_registry
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


def _skills_catalog() -> str:
    """可用技能清单（`AGENTS.md §9.6`）。

    常驻上下文的是**清单**（几行 id + 适用场景），正文由 `study_skill` 工具按需取回。
    技能库在启动时一次性装好、之后只读，所以这里直接取全局单例——
    为一个静态字符串再给 graph state 加字段不划算。

    注意：tavern 模式（ST 预设组装）**不注入**这一层——那个模式只用酒馆预设 +
    角色卡还原原生体验（§8.1）。
    """
    return get_skill_registry().catalog_text()


def _join_system_messages(messages: list[dict[str, str]]) -> str:
    """把全部 system 消息拼成一段（调试 / 界面预览用）。

    与 `RenderedPrompt.system_prompt` 同口径——但那条属性只看得见渲染阶段的 messages，
    酒馆路径在渲染之后还会追加输出形态约束，所以预览必须按**最终** messages 重新拼，
    否则界面上看到的提示词与真正发出去的不一致。
    """
    return "\n\n".join(
        message["content"] for message in messages if message.get("role") == "system"
    )

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


def _join_blocks(*blocks: str) -> str:
    """用空行连接非空文本块（角色层 = 人设正文 + 背景故事）。"""
    return "\n\n".join(block.strip() for block in blocks if block.strip())


#: 强制补调情绪时的 max_tokens 上限。
#:
#: 这一调用**只需要一个 JSON**：回复正文在上一轮已经生成，且会被原样沿用
#: （见 `generate_reply` 里的 `result.reply = sanitize_reply(agent.reply or ...)`），
#: 让模型再写一遍完整回复纯属白付一次生成。留 300 是为了给 JSON 结构 +
#: 简短 reply 占位留足余量——截断会让解析失败、退化成关键词兜底，
#: 那比多花几十 token 更亏。
_EMOTION_PROBE_MAX_TOKENS = 300


def _emotion_probe_messages(state: ChatState, reply: str) -> list[ChatMessage]:
    """强制补调情绪时用的**极简**消息序列。

    刻意**不带 system prompt**（人设 / 世界书 / 记忆 / 技能清单）：
    情绪判定只依赖「用户说了什么」与「我回了什么」，与角色设定无关。
    而完整提示词每轮数千 token，重发一次就是白付一遍 prefill
    （远程端点上数百毫秒量级，且与生成时间叠加）。

    末尾必须是**指令消息**而非 assistant 结尾：实测仅以 assistant 结尾时，
    部分 OpenAI 兼容实现不会返回 tool_call（3 次中 2 次落回关键词兜底）。
    指令里保留用户原话，避免「最后一条用户消息」丢失真实输入。
    """
    return [
        ChatMessage(
            role="user",
            content=(
                f"【用户发言】{state.get('user_input', '')}\n"
                f"【已经发出的回复】{reply}\n\n"
                "请调用工具，只判定「用户此刻的情绪」与强度。"
                "reply 字段填一句「已回复」占位即可，不要重写上面的回复正文。"
            ),
        )
    ]


def _emotion_probe_kwargs(kwargs: dict) -> dict:
    """补调情绪的采样参数：沿用本轮设置，但把额度压到 JSON 够用的量级。

    开了思考（enable_thinking）的模型不动额度：思考 token 会先吃掉预算，
    压小反而会让 JSON 完全出不来（比多花时间更糟）。本部署默认关闭思考，
    因此常规路径都能吃到这个优化。
    """
    if kwargs.get("enable_thinking"):
        return kwargs
    probe = dict(kwargs)
    current = probe.get("max_tokens")
    if current is None or current > _EMOTION_PROBE_MAX_TOKENS:
        probe["max_tokens"] = _EMOTION_PROBE_MAX_TOKENS
    return probe


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
        jailbreaks: dict[str, JailbreakPreset] | None = None,
        default_jailbreak_id: str = "",
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
        # 叙事框架预设（jailbreak，与 persona / style 正交）：
        # `default_jailbreak_id` 为空串即「部署默认关闭」——这是出厂口径，
        # 不是缺省值遗漏。单个请求仍可用 state.jailbreak_id 主动打开某一档。
        self.jailbreaks = jailbreaks or {}
        self.default_jailbreak_id = default_jailbreak_id
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

    def _ensure_thinking_headroom(self, sampling: ResolvedSampling) -> ResolvedSampling:
        """推理模型：抬高 max_tokens 下限（思考 token 会先吃掉额度）。

        为什么放在采样解析的最后一步：文风预设的采样优先级最高，会把
        `max_tokens` 压到 350 这类小值；对推理模型而言那等于「只准思考、不准说话」——
        实测 deepseek-flash 在 max_tokens=350 时正文恒为空（思考占了 582 token）。

        识别分两条腿：模型名匹配（覆盖已知）+ 运行期观测（provider 见过
        `reasoning_content` 就标记），因为存在名字里没有标记的推理模型。
        """
        model = getattr(self.llm, "model", "") or ""
        observed = bool(getattr(self.llm, "saw_reasoning", False))
        if not (is_reasoning_model(model) or observed):
            return sampling

        adjusted = ensure_reasoning_headroom(
            sampling.max_tokens, model, assume_reasoning=observed
        )
        if adjusted == sampling.max_tokens:
            return sampling
        return replace(sampling, max_tokens=adjusted)

    # ---------- ① 人设渲染 ----------

    def load_persona(self, state: ChatState) -> dict:
        """渲染角色人设 + 背景故事（状态变量替换）。

        角色层 = 人设正文 + 可选背景故事，顺序固定：

            人设正文 → [背景故事] → （世界书 / 记忆 / 滚动窗口等后续层）

        背景故事来自用户自建的角色卡（内置角色默认没有）。单独成块是为了让模型
        分得清「说话的人是谁、怎么说话」与「这个世界的设定」。
        """
        persona = self.presets.get(state["persona_id"])
        if persona is None:
            # 预设快照里没有这个角色（如刚被删除、图尚未重建）：明确告警比
            # KeyError 把整轮对话打掉更好——用户至少能看到回复与原因
            return {
                "persona_text": "",
                "warnings": self._merge_warnings(
                    state,
                    [f"人设 {state['persona_id']} 不在已加载的预设中，本轮未注入人设"],
                ),
            }
        context_vars = {
            "user_name": state.get("user_name", "朋友"),
            # 角色名取**本轮人设**：注册表默认值只是兜底，用兜底值渲染 {{char_name}}
            # 会让模型在提示词里把角色叫成「角色」（酒馆卡尤其依赖这个宏）
            "char_name": persona.name,
            **state.get("state_vars", {}),
        }
        rendered = render_persona_prompt(persona, context_vars)
        background = render_persona_background(persona, context_vars)
        return {
            "persona_text": _join_blocks(rendered.text, background.text),
            "warnings": self._merge_warnings(
                state, [*rendered.warnings, *background.warnings]
            ),
        }

    # ---------- ② 世界书命中 ----------

    def worldbook_recall(self, state: ChatState) -> dict:
        """关键词 / 正则 / 语义向量三通道触发 + 按档位编排（priority + 预算）。

        按 `companion_id` 过滤归属：`scope="*"` 的条目对所有角色生效，
        用户为某个角色写的专属设定只在该角色下注入（见 WorldBookEntry.scope）。

        命中结果按 **注入档位**分开落点（见 `app/prompts/assemble`）：
        人设前 / 人设后各一块，`at_depth` 档则按 depth 插进对话历史。
        """
        hits = match_entries(
            self.entries,
            state.get("user_input", ""),
            vector_index=self.worldbook_index,
            companion_id=state.get("companion_id", state.get("persona_id", "")),
        )
        placement = place_worldbook(hits, self.worldbook_budget)
        return {
            "worldbook_hits": hits,
            "worldbook_skipped": placement.skipped,
            "worldbook_text": placement.after_char,
            "worldbook_before_text": placement.before_char,
            "worldbook_depth": placement.depth,
            "warnings": self._merge_warnings(state, placement.warnings),
        }

    # ---------- ②′ 个人记忆检索 ----------

    def knowledge_recall(self, state: ChatState) -> dict:
        """个人记忆检索（混合检索：BM25 + 稠密向量 → RRF）。

        与情景记忆共用检索底座，但**不做时间衰减、不做情绪加权**——
        知识不老化，且是客观的。这层是「用户给过我的资料」，
        与「我们一起经历过什么」（情景记忆）职责不同。

        **酒馆世界书只在酒馆聊天模式下、且只查「当前挂载」的那几本**
        （AGENTS.md §8.2）：每本世界书一个独立作用域（`tavern:book:{哈希}`），
        挂载清单由 `load_mounted_books()` 统一读取——**默认空 = 一本都不查**。
        桌宠模式（`include_tavern=False`）更是一个世界书作用域都不查：否则内置人设
        会突然「知道」别的作品的角色设定，既污染角色，又让人误以为召回坏了。

        为什么不把清单塞进图状态由路由传入：挂载是**全局当前挂载**（一份），
        不是会话属性；由节点自己读，路由层就不必知道这个配置的存在。
        """
        if self.knowledge is None:
            return {"knowledge_lines": []}

        companion_id = state.get("companion_id", state.get("persona_id", ""))
        mode = normalize_mode(state.get("mode"))
        tavern_mode = mode == MODE_TAVERN
        # 只在酒馆模式下才去读清单：桌宠模式读了也没用（作用域一个都不加），
        # 省一次文件读取。清单读失败降级为空（见 mounted.load_mounted_books）。
        mounted = load_mounted_books() if tavern_mode else []
        try:
            hits = self.knowledge.retrieve(
                companion_id,
                state.get("user_input", ""),
                include_tavern=tavern_mode,
                mounted_books=mounted,
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

    def _resolve_jailbreak(self, state: ChatState) -> tuple[str, str, list[str]]:
        """解析本轮的叙事框架层，返回 `(生效的预设 id, 框架块正文, 告警)`。

        **默认关闭**。三种表达方式语义各不相同，不要混用：

            state.jailbreak_id 未传 / 空串 → 用部署默认（`default_jailbreak_id`，
                                            关闭时即空串 = 本层不产出任何字符）
            state.jailbreak_id == "none"   → 本轮强制关闭，覆盖部署默认
            state.jailbreak_id == 其它值   → 本轮显式指定档位，**同时隐含启用**
                                            （用户可以在单轮里主动开一档）

        为什么允许单轮主动开：开关是部署级的，但「这一轮想认真跑一段剧情」是
        会话级的临时意图。强制先改 .env 再回来聊，会把一次即兴需求变成一次运维。

        与 ST 预设路径的关系：本层**只作用于内置分层路径**。走导入的 ST 预设时，
        那个预设自带 `jailbreak` 槽位（ST 的 Post-History Instructions），
        再叠一层本项目的框架会变成两套框架互相打架，且会破坏 §5–§7 的组装契约。
        酒馆模式本来就具备这一能力，不需要本层补。
        """
        raw = state.get("jailbreak_id")
        if raw is None or raw == "":
            preset_id = self.default_jailbreak_id
        elif raw == "none":
            preset_id = ""
        else:
            preset_id = raw

        if not preset_id or not self.jailbreaks:
            return "", "", []

        preset = self.jailbreaks.get(preset_id)
        if preset is None:
            return "", "", [f"未知叙事框架预设「{preset_id}」，已跳过该层"]

        persona = self.presets.get(state["persona_id"])
        warnings = check_jailbreak_conflicts(persona, preset) if persona else []
        return preset.id, preset.instruction_block, warnings

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

        # 叙事框架层（默认关闭）：关闭时 jailbreak_text 为空串，该层零开销
        jailbreak_id, jailbreak_text, jailbreak_warnings = self._resolve_jailbreak(state)

        # 模型预设档总要解析：采样参数与推理开关都来自它，
        # 文风预设的 sampling 仅在其之上覆盖（因此 style 缺失时也不能跳过）。
        sampling: ResolvedSampling = resolve_sampling(
            self.model_name,
            style.sampling if style is not None else None,
            profiles=self.profiles,
            preset_id=state.get("preset_id") or None,
        )
        # 推理模型的额度下限：文风预设可能刚把 max_tokens 压得很小
        sampling = self._ensure_thinking_headroom(sampling)

        if style is not None:
            style_text = self._compose_style_text(style, sampling.style_hint)
            examples = [(e.user, e.assistant) for e in style.examples]

        built = self.prompt_manager.build(
            persona_text=state.get("persona_text", ""),
            user_input=state.get("user_input", ""),
            worldbook_text=state.get("worldbook_text", ""),
            worldbook_before_text=state.get("worldbook_before_text", ""),
            worldbook_depth=state.get("worldbook_depth", []),
            skills_text=_skills_catalog(),
            # 感知·此刻：路由层渲染好放进 state（默认空串 → 该层零开销）
            perception_text=state.get("perception_text", ""),
            knowledge_lines=state.get("knowledge_lines", []),
            warm_lines=state.get("warm_lines", []),
            fact_lines=state.get("fact_lines", []),
            history=state.get("history", []),
            style_text=style_text,
            # 主动开口的行为约束：非主动轮为空串
            proactive_text=state.get("proactive_text", ""),
            jailbreak_text=jailbreak_text,
            examples=examples,
        )
        return {
            "system_prompt": built.system_prompt,
            "messages": built.messages,
            "sampling": sampling,
            "style_id": style.id if style else "",
            "jailbreak_id": jailbreak_id,
            "example_count": built.example_count,
            "warnings": self._merge_warnings(
                state, [*style_warnings, *jailbreak_warnings, *built.warnings]
            ),
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
            # use_sysprompt 开启时用它替换系统条目正文（空串 = 不替换，只告警）
            system_prompt_override=st_preset.system_prompt_override,
            persona_name=persona.name if persona is not None else "",
            user_name=state.get("user_name", ""),
            macro_variables=dict(state.get("macro_variables", {})),
        )
        rendered = render_st_preset(st_preset, context)
        # at_depth 档与内置路径用**同一个**插入实现：两条组装路径的 depth 语义
        # 必须一致，否则同一个世界书在两种模式下的行为会不一样。
        messages = insert_at_depth(rendered.messages, state.get("worldbook_depth", []))

        # 输出形态（全局强制）：本路径不经过 PromptManager 的 LAYER_FORMAT，
        # 就在这里补一条**末尾** system 约束。两个取舍：
        #   ① 位置取最末——对齐 ST 的 Post-History Instructions（紧贴生成，对输出形态
        #      影响最强），也正是 PromptManager 把 LAYER_FORMAT 放 system 最末的同一条理由；
        #   ② 在 at_depth 之后追加——否则深度插入的锚点会漂移：`depth=1` 会从
        #      「插在用户输入之前」变成「插在约束块之前」，两条路径的 depth 语义就不一致了。
        #
        # 感知层与主动开口层同理在这里补：本路径不走 PromptManager，
        # 若只在内置路径注入，「桌宠模式 + 挂酒馆预设」这个**合法组合**
        # （AGENTS.md §8.1）就会看不到此刻的感知、也拿不到主动开口的约束。
        # 位置同样取末尾（紧贴生成），顺序为「感知 → 主动开口 → 形态」：
        # 先交代处境、再交代这一轮是什么情况、最后压输出形态。
        trailing: list[dict[str, str]] = []
        perception_text = (state.get("perception_text") or "").strip()
        if perception_text:
            trailing.append(
                {"role": "system", "content": f"[{PERCEPTION_SECTION}]\n{perception_text}"}
            )
        proactive_text = (state.get("proactive_text") or "").strip()
        if proactive_text:
            trailing.append({"role": "system", "content": proactive_text})
        trailing.append({"role": "system", "content": DIALOGUE_ONLY_RULE})
        messages = [*messages, *trailing]

        return {
            "system_prompt": _join_system_messages(messages),
            "messages": messages,
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

        return self._ensure_thinking_headroom(
            ResolvedSampling(
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
        """世界书内容投给哪个 marker 槽位（返回 before/after 与告警）。

        两个档位正好对上 ST 的两个槽位（与 ST 自己的语义一致）：
        `before_char`（人设前的设定）→ `worldInfoBefore`；
        其余档位 → `worldInfoAfter`。
        注意 `worldbook_text` 是本项目内置/自建条目的默认档（`after_char`），
        因此它们在 ST 预设下的落点从 `worldInfoBefore` 移到了 `worldInfoAfter`——
        两个槽位在 ST 默认顺序里紧贴角色定义，属位置微调，不是内容丢失。

        槽位没启用时**合并投给另一个**：宁可就错位置，也不能静默丢掉命中。
        两个都没启用才告警。
        """
        before = state.get("worldbook_before_text", "")
        after = state.get("worldbook_text", "")
        if not (before.strip() or after.strip()):
            return "", "", []

        enabled = {entry.identifier for entry in st_preset.order if entry.enabled}
        if MARKER_WORLD_INFO_BEFORE in enabled and MARKER_WORLD_INFO_AFTER in enabled:
            return before, after, []
        merged = _join_blocks(before, after)
        if MARKER_WORLD_INFO_BEFORE in enabled:
            return merged, "", []
        if MARKER_WORLD_INFO_AFTER in enabled:
            return "", merged, []
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

        两通道都保证给出一个情绪结果；若模型确实没产出正文（如推理模型的额度
        被思考吃光），会通过 warnings 说明原因，而不是静默返回空字符串。
        """
        messages = [ChatMessage(**m) for m in state.get("messages", [])]
        # 清掉上一轮可能残留的诊断说明（provider 实例是全局共享的）
        self.llm.take_generation_note()
        # 本轮要带给用户的生成提示（强制工具调用失败、空回复原因等）
        extra_warnings: list[str] = []
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
        # 本轮真实的 LLM 调用次数：工具循环的轮数 + 后续每一次补调/重试。
        # 它是「回复为什么慢」最直接的解释量——每多一次调用就多付一遍
        # prefill + decode（远程端点上通常是秒级）。
        llm_calls = max(1, int(getattr(agent, "rounds", 1) or 1))

        # ① 终止工具（情绪）返回 → 解析出回复与情绪
        if agent.emotion_call:
            result = parse_emotion_result(agent.emotion_call)
            if result is not None:
                # 兜底清理：剔除 markdown/emoji 与模型的元评论（自我纠正）
                result.reply = sanitize_reply(result.reply)
                return {
                    "reply": result.reply,
                    "emotion": result,
                    "tools_used": tools_used,
                    "llm_rounds": llm_calls,
                }

        # ② 模型只给了纯文本、未调用情绪工具 → **强制补一次结构化输出**。
        #    否则会直接落到关键词兜底：未命中关键词时情绪恒为 neutral / intensity 0.5
        #    （实测缺陷：前端情绪强度「始终 50%」）。靠提示词无法保证模型一定调工具，
        #    故此处用 tool_choice 强制（仅多一次调用，且只在模型未调工具时发生）。
        #
        #    这次补调刻意**只发极简消息 + 压小额度**（见 _emotion_probe_messages /
        #    _emotion_probe_kwargs）：原实现把整份 system prompt 重发一遍、并要求模型
        #    再写一遍完整回复，而那份回复随后会被 agent.reply 覆盖——等于白付
        #    一次完整生成的 prefill + decode，是「回复慢」里最不值得花的一笔。
        if agent.reply:
            try:
                forced_calls = self.llm.chat_with_tools(
                    _emotion_probe_messages(state, agent.reply),
                    [build_emotion_tool()],
                    tool_choice={
                        "type": "function",
                        "function": {"name": EMOTION_TOOL_NAME},
                    },
                    **_emotion_probe_kwargs(kwargs),
                )
            except Exception as exc:  # noqa: BLE001 —— 模型侧拒绝不该打断整轮对话
                # 实测：部分推理模型（DeepSeek 系）不接受强制 tool_choice，服务端直接 400
                # （"Thinking mode does not support this tool_choice"）。此时回复文本
                # （agent.reply）已经拿到，不该整轮失败 —— 退回关键词兜底即可。
                forced_calls = None
                extra_warnings.append(
                    f"强制结构化情绪输出失败（{exc}），本轮情绪改用关键词兜底"
                )
            # 被拒绝的那次也算一次真实往返（服务端 400 也要走一趟网络）
            llm_calls += 1
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
                        "llm_rounds": llm_calls,
                    }

        # ③ 终极降级：普通回复 + 关键词兜底情绪（用真实回复替换占位文本）
        if agent.reply:
            reply = sanitize_reply(agent.reply)
        else:
            llm_calls += 1
            reply = sanitize_reply(self.llm.chat(messages, **kwargs))
        note = self.llm.take_generation_note()
        if not reply and note:
            # 拿到空回复且已知原因（推理模型的思考吃光了额度）→ 放大额度重试一次。
            # 不重试的话，在 provider 下次观测到 reasoning_content、自动抬高额度之前，
            # 用户每句都会收到空白 —— 实测就是「第一句话没反应」。
            wider = {
                **kwargs,
                "max_tokens": ensure_reasoning_headroom(
                    kwargs.get("max_tokens"),
                    getattr(self.llm, "model", ""),
                    assume_reasoning=True,
                ),
            }
            llm_calls += 1
            retry = sanitize_reply(self.llm.chat(messages, **wider))
            if retry:
                reply = retry
                note = ""
                self.llm.take_generation_note()   # 清掉上一轮的说明，避免污染下次
        if note:
            # 空回复必须能被解释：provider 知道 finish_reason 与推理 token 占用
            extra_warnings.append(note)
        fallback = extract_emotion_fallback(state.get("user_input", ""))
        fallback.reply = reply
        return {
            "reply": reply,
            "emotion": fallback,
            "tools_used": tools_used,
            "llm_rounds": llm_calls,
            "warnings": self._merge_warnings(state, extra_warnings),
        }

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
