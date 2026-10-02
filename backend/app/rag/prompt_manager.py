"""PromptManager：分层提示词组装 + 优先级预算管理（M3 正式实现）。

设计参照①的固定组装顺序与优先级（高 → 低）：

    人设 > 世界书命中 > 【感知·此刻】 > 可用技能 > 个人记忆 > 向量召回
      > 结构化事实 > 滚动窗口 > 本次输入

**感知·此刻**（`LAYER_PERCEPTION`，见 `docs/proactive-multimodal.md` §4.5）
放在世界书之后、技能与记忆之前：它是最「当下」的信息，但**易失**（带 TTL），
因此不该压过设定与记忆这些长期资产。无感知源时该层为空串，
组装结果与不装感知时逐字一致（有回归测试守着）。

可用技能（§9.6）放在世界书之后、记忆之前：它是**能力说明**而不是记忆——
告诉模型「遇到这类情况该怎么做」，与「关于这个人/这段关系知道什么」是两类信息，
混进记忆块会让两者都变浑。

预算策略（两级）：
1. 层内预算：世界书块、记忆块、历史窗口各自独立预算，超出即截断；
2. 总量预算：总和超过 total_budget 时，从**最低优先级层**开始裁减
   （历史窗口先裁最旧消息 → 记忆块尾部 → 世界书尾部），
   人设与本次输入为必留层（mandatory），永不裁剪。

输出 BuiltPrompt：可直接转 OpenAI 风格 messages，并带各层 token 统计，
供 LangGraph 节点与调试使用。

末尾还有一层**输出约束**（`LAYER_FORMAT`：回复的形态 + 字数上限）。它不是「内容」
而是「形态」，因此刻意放在 system 最末（比文风更贴近输入，压制模型写满额度、
自己补旁白的倾向），也不参与裁剪——几十个字换整段回复的形态，怎么算都划算。

形态那条（`DIALOGUE_ONLY_RULE`：只说话，不写旁白 / 动作 / 神情 / 心理描写）
与 `prompts/adaptation/rules.yaml` 的 `target.dialogue_only` 同源，且是**全局强制**：
内置分层路径由本层承载；酒馆预设路径不经过本模块，在 `graph/nodes.py` 里
捎带进文风块（该块本就承载 `style_hint` 这类技术性输出约束，位置与语义都合适）。

另有可选的**叙事框架层**（`LAYER_JAILBREAK`，术语对齐 ST 的 jailbreak 槽位）。
它声明「这段对话处在什么框架里」（虚构叙事 / 现实问答），**默认关闭**——
关闭时 `jailbreak_text` 为空串，本层不产生任何字符，组装结果与加入本层之前逐字一致。
开启后排在 system **最前**：框架先于内容，人设与世界设定才会被模型放在这个框架下解读
（ST 的 Main Prompt 承载同类声明，同样在最前）。

为什么它不放在末尾（ST 的 Post-History Instructions 位置）：ST 那套是「多消息 + 最后
一条 system 紧贴生成」，本项目内置路径只有**一条** system 消息，末尾已被回复格式约束
占住；而框架层要影响的是「怎么理解后面的设定」，不是「这一轮说多长、要不要写旁白」，故取首位。
需要 ST 那种「临近输入施加强影响」的效果时，走 ST 预设组装路径——那条路径本身就有
jailbreak 槽位（见 `app/graph/nodes.py` 的 `_assemble_with_st_preset`）。
"""

from dataclasses import dataclass, field

from app.prompts.assemble import DepthInjection, insert_at_depth
from app.prompts.renderer import estimate_tokens
from app.session.context import ChatTurn

# ---- 层标识 ----
LAYER_WORLDBOOK_BEFORE = "worldbook_before"  # 世界书命中·人设前档（before_char）
LAYER_PERSONA = "persona"        # 角色人设（必留）
LAYER_WORLDBOOK = "worldbook"    # 世界书命中
LAYER_PERCEPTION = "perception"  # 感知·此刻（易失事实，带 TTL；见 docs/proactive-multimodal.md §4.5）
LAYER_SKILLS = "skills"          # 可用技能清单（§9.6，仅列出 id/名称/适用场景）
LAYER_KNOWLEDGE = "knowledge"    # 个人记忆（用户上传的语料）
LAYER_WARM = "warm_recall"       # 温层向量召回
LAYER_FACTS = "cold_facts"       # 冷层结构化事实
LAYER_HISTORY = "history"        # 滚动窗口（可裁最旧）
LAYER_USER = "user_input"        # 本次输入（必留）
LAYER_STYLE = "style"            # 表达风格（M5，放 system 末尾：越靠近输入影响越强）
LAYER_PROACTIVE = "proactive"    # 主动开口的行为约束（仅主动轮非空，见 §5.4）
LAYER_FORMAT = "format"          # 输出格式约束：形态（只说话）+ 长度（放**最末**，比风格更贴近输入）
LAYER_JAILBREAK = "jailbreak"    # 叙事框架（可选，默认关闭；开启时放 system **最前**）

# ---- 段落标题 ----
ROLE_DEF_SECTION = "角色人设"
JAILBREAK_SECTION = "叙事框架"    # 术语对齐 ST 的 jailbreak 槽位（见模块文档）
WORLDBOOK_BEFORE_SECTION = "背景设定"   # before_char 档：排在人设**之前**
WORLDBOOK_SECTION = "场景补充"
#: 感知层标题。**刻意用「此刻」而不是「系统感知」**：前者是处境，后者是机器口吻——
#: 后一种措辞会让模型把注意力放在「我有什么能力」上，而不是「这个人此刻在做什么」。
PERCEPTION_SECTION = "此刻"
SKILLS_SECTION = "可用技能"       # 只有清单；正文由 study_skill 工具按需取回
KNOWLEDGE_SECTION = "参考资料"   # 个人记忆独立成块，不并入「记忆回忆」
MEMORY_SECTION = "记忆回忆"
FORMAT_SECTION = "回复格式"

# ---- 默认预算 ----
DEFAULT_WORLDBOOK_BUDGET = 400
DEFAULT_SKILLS_BUDGET = 400      # 技能清单本身必须便宜（几行 id + 适用场景）
#: 感知层预算。**刻意小**：感知是易失的补充信息，不该挤掉记忆与世界书。
#: 屏幕 OCR 这类长文本若不加限制，一条就能吃掉整个总量预算。
DEFAULT_PERCEPTION_BUDGET = 120
DEFAULT_KNOWLEDGE_BUDGET = 900   # 个人记忆是用户主动提供的，给足预算
DEFAULT_MEMORY_BUDGET = 400
DEFAULT_HISTORY_BUDGET = 700
DEFAULT_TOTAL_BUDGET = 4000
DEFAULT_STYLE_BUDGET = 400     # 风格块预算（含示例对话）
DEFAULT_JAILBREAK_BUDGET = 600  # 叙事框架块预算（一段声明 + 行为指令 + 边界，实测 550~900 字）
DEFAULT_PROACTIVE_BUDGET = 400  # 主动开口的行为约束块（固定文案，约 200 字）

#: 单次回复的字数上限（产品口径，与 `prompts/adaptation/rules.yaml` 的
#: `target.reply_length` 同值）：一次回应 2~4 句才像「打电话」，
#: 而不是念一段小说。<= 0 表示不加这条约束（形态约束仍然生效）。
DEFAULT_REPLY_CHAR_LIMIT = 100

#: 输出形态：**只说话，不写旁白**（全局强制的产品口径，无开关）。
#:
#: 与 `prompts/adaptation/rules.yaml` 的 `target.dialogue_only` 同源——那条口径
#: 原先只作用于「导入酒馆预设」的适配路径（用来体检 / 改写小说预设），内置分层路径
#: 完全没有，于是默认文风下模型会自己补旁白与动作（`*她轻轻笑了笑*`、
#: `（他把杯子推过来）`、`心里想着……`），把一对一陪伴变成念小说。
#: 文风预设管的是「怎么说话」（内容意图），管不住「要不要写旁白」——这是形态问题。
#:
#: 单一事实来源：内置路径的 `LAYER_FORMAT` 与酒馆路径的文风块都引用本常量，
#: 改文案只需改这一处（`graph/nodes.py` 的 `OUTPUT_FORM_RULE` 别名指向它）。
DIALOGUE_ONLY_RULE = (
    "只输出你对用户说的「话」本身：不写旁白、动作、神情、环境，也不写心理描写。\n"
    "不要出现「她轻轻笑了笑」「（他把杯子推过来）」「心里想着……」这类描述，\n"
    "也不要用星号或括号把动作包起来——像面对面说话那样，直接说你要说的那句话。"
)

# 层优先级（数字越大越重要，裁剪时从最小开始）
_LAYER_PRIORITY = {
    LAYER_PERSONA: 100,
    LAYER_USER: 100,
    LAYER_FORMAT: 95,     # 输出格式：形态 + 长度，**不参与裁剪**（见 build() 的 _total()）
    # 主动开口的行为约束：**本轮显式发生的事**，比风格更靠前。
    # 它只在主动轮存在（空串即零开销），存在时就不该被预算挤掉——
    # 被挤掉的后果是模型把「系统触发」当成用户说的话，回一句莫名其妙的东西。
    LAYER_PROACTIVE: 93,
    LAYER_JAILBREAK: 92,
    LAYER_STYLE: 90,      # 表达风格很重要，但在总量不足时仍可裁（排在必留层之后）
    LAYER_WORLDBOOK_BEFORE: 85,  # 人设前的设定：比场景补充更「定义性」，但仍低于人设
    LAYER_WORLDBOOK: 80,
    # 感知·此刻：排在世界书之后、技能与记忆之前。
    # 理由见 docs/proactive-multimodal.md §4.5——它是最「当下」的信息，
    # 但**易失**（带 TTL），因此不该压过设定与记忆这些长期资产。
    LAYER_PERCEPTION: 78,
    LAYER_SKILLS: 75,     # 能力说明；比记忆层重要，但不如世界书的场景设定
    LAYER_KNOWLEDGE: 70,  # 用户主动上传 > AI 自动记录的对话回忆
    LAYER_WARM: 60,
    LAYER_FACTS: 55,
    LAYER_HISTORY: 20,
}


def _render_memory_block(layers: dict[str, "PromptLayer"]) -> str:
    """渲染记忆块（相关回忆 > 已知事实）。

    唯一实现：BuiltPrompt.memory_block 与 build() 均调用本函数，避免不一致。
    """
    parts: list[str] = []
    for key in (LAYER_WARM, LAYER_FACTS):
        layer = layers.get(key)
        if layer is not None and not layer.empty:
            parts.append(f"{layer.title}\n{layer.body}")
    if not parts:
        return ""
    return "\n".join([f"[{MEMORY_SECTION}]", *parts])


@dataclass
class PromptLayer:
    """一个提示层。"""

    key: str
    title: str
    body: str = ""
    priority: int = 0
    mandatory: bool = False
    tokens: int = 0
    truncated: bool = False

    @property
    def empty(self) -> bool:
        return not self.body.strip()


@dataclass
class BuiltPrompt:
    """PromptManager 的组装结果。"""

    system_prompt: str
    messages: list[dict[str, str]]
    layers: dict[str, PromptLayer] = field(default_factory=dict)
    history: list[ChatTurn] = field(default_factory=list)
    total_tokens: int = 0
    warnings: list[str] = field(default_factory=list)
    dropped_layers: list[str] = field(default_factory=list)
    example_count: int = 0   # 注入的示例对话组数（few-shot，用于调试/展示）

    @property
    def memory_block(self) -> str:
        """记忆块文本（温层召回 > 事实 > 摘要，用于结果回传与调试）。"""
        return _render_memory_block(self.layers)


class PromptManager:
    """分层组装器：层内预算 + 总量预算 + 优先级裁剪。"""

    def __init__(
        self,
        *,
        worldbook_budget: int = DEFAULT_WORLDBOOK_BUDGET,
        skills_budget: int = DEFAULT_SKILLS_BUDGET,
        perception_budget: int = DEFAULT_PERCEPTION_BUDGET,
        knowledge_budget: int = DEFAULT_KNOWLEDGE_BUDGET,
        memory_budget: int = DEFAULT_MEMORY_BUDGET,
        history_budget: int = DEFAULT_HISTORY_BUDGET,
        total_budget: int = DEFAULT_TOTAL_BUDGET,
        style_budget: int = DEFAULT_STYLE_BUDGET,
        jailbreak_budget: int = DEFAULT_JAILBREAK_BUDGET,
        proactive_budget: int = DEFAULT_PROACTIVE_BUDGET,
        reply_char_limit: int = DEFAULT_REPLY_CHAR_LIMIT,
    ) -> None:
        self.worldbook_budget = worldbook_budget
        self.skills_budget = skills_budget
        self.perception_budget = perception_budget
        self.knowledge_budget = knowledge_budget
        self.memory_budget = memory_budget
        self.history_budget = history_budget
        self.total_budget = total_budget
        self.style_budget = style_budget
        self.jailbreak_budget = jailbreak_budget
        self.proactive_budget = proactive_budget
        self.reply_char_limit = reply_char_limit

    # ---------- 内部：按预算截断文本/列表 ----------

    @staticmethod
    def _fit_lines(lines: list[str], budget: int) -> tuple[list[str], bool]:
        """按预算逐条装入，返回 (装入的行, 是否发生截断)。"""
        kept: list[str] = []
        used = 0
        for line in lines:
            cost = estimate_tokens(line)
            if used + cost > budget:
                return kept, True
            kept.append(line)
            used += cost
        return kept, False

    @staticmethod
    def _fit_text(text: str, budget: int) -> tuple[str, bool]:
        """按预算从尾部截断文本。"""
        if estimate_tokens(text) <= budget:
            return text, False
        step = max(1, len(text) // 20)
        clipped = text
        while estimate_tokens(clipped) > budget and clipped:
            clipped = clipped[:-step] if len(clipped) > step else ""
        return clipped, True

    def _fit_history(
        self, history: list[ChatTurn], budget: int
    ) -> tuple[list[ChatTurn], bool]:
        """历史窗口：保留最近的消息（从尾部往回取）。"""
        kept: list[ChatTurn] = []
        used = 0
        for turn in reversed(history):
            cost = estimate_tokens(turn.text)
            if used + cost > budget:
                return list(reversed(kept)), True
            kept.append(turn)
            used += cost
        return list(reversed(kept)), False

    # ---------- 主流程 ----------

    def format_text(self) -> str:
        """输出格式约束块 = 形态（只说话）+ 长度（`reply_char_limit <= 0` 时只留形态）。

        为什么值得单独成层：文风预设管的是「怎么说话」，但**要不要写旁白**与**说多长**
        都是产品口径（像打电话，不像念小说段落）。这两条原先都只存在于酒馆预设的
        适配规则里（`prompts/adaptation/rules.yaml` 的 `target.dialogue_only` /
        `target.reply_length`），内置分层路径完全没有——于是默认文风下模型会自己补
        旁白动作，并倾向于写满 `max_tokens`，一屏都装不下。放 system **最末**
        （比风格更贴近输入）效果最明显。

        形态恒开（`DIALOGUE_ONLY_RULE`，全局强制的产品口径）；长度可关，
        但关掉长度不会连形态一起关——它们是同一层的两条独立约束。
        """
        parts = [DIALOGUE_ONLY_RULE]
        if self.reply_char_limit > 0:
            parts.append(
                f"一次回复不超过 {self.reply_char_limit} 字：2~4 句话，先说最要紧的那一点。\n"
                "不铺陈背景、不列点、不做总结陈词；想说的没说完就先问一句，"
                "等对方回应再接着说。"
            )
        return "\n".join(parts)

    def build(
        self,
        *,
        persona_text: str,
        user_input: str,
        worldbook_text: str = "",
        worldbook_before_text: str = "",
        worldbook_depth: list[DepthInjection] | None = None,
        skills_text: str = "",
        perception_text: str = "",
        knowledge_lines: list[str] | None = None,
        warm_lines: list[str] | None = None,
        fact_lines: list[str] | None = None,
        history: list[ChatTurn] | None = None,
        style_text: str = "",
        proactive_text: str = "",
        jailbreak_text: str = "",
        examples: list[tuple[str, str]] | None = None,
    ) -> BuiltPrompt:
        """组装完整提示。

        参数均为**已渲染好**的各层内容（召回与变量替换由调用方完成），
        本方法只负责分层编排、预算与裁剪。

        参数:
            worldbook_before_text: `before_char` 档的设定，排在角色人设**之前**
                （ST 的 worldInfoBefore 语义）；
            worldbook_depth: `at_depth` 档的注入，按 depth 插进对话历史
                （语义见 `app/prompts/assemble.insert_at_depth`）；
            skills_text: 可用技能清单（只有 id / 名称 / 适用场景，由 `SkillRegistry.catalog_text()`
                         产出）；正文不在这里——那是 `study_skill` 的事；
            perception_text: 感知·此刻（已渲染好的 `- …` 行，由 `app/perception/context.py`
                         唯一产出）。**默认空串**：没有感知源时该层零开销，
                         组装结果与不装感知时逐字一致（与 jailbreak 层同一约定）；
            style_text: 表达风格指令块，放在 **system 末尾**（越靠近输入影响越强）；
            proactive_text: 主动开口的行为约束块（仅主动轮非空，由
                         `app/proactive/prompt.py` 产出）。块内自带标题，
                         与文风块同约定，此处不再加头；
            jailbreak_text: 叙事框架块（`app/prompts/jailbreak/` 的预设产出）。
                **空串即关闭**（默认），此时本层不占任何 token、不影响组装结果。
                非空时排在 system **最前**（见模块文档的取舍说明）。
            examples: 示例对话（few-shot），以真实消息对插在历史之前。
        """
        warnings: list[str] = []
        history = list(history or [])
        examples = list(examples or [])
        worldbook_depth = list(worldbook_depth or [])

        # ---- ⓪ 示例对话预算控制（超出则从后截断） ----
        kept_examples: list[tuple[str, str]] = []
        used_example_tokens = 0
        for user_text, assistant_text in examples:
            cost = estimate_tokens(user_text) + estimate_tokens(assistant_text)
            if used_example_tokens + cost > max(self.style_budget // 2, 60):
                warnings.append("示例对话超出预算，已截断部分示例" )
                break
            kept_examples.append((user_text, assistant_text))
            used_example_tokens += cost

        # ---- ① 层内预算 ----
        wb_before_body, wb_before_cut = self._fit_text(
            worldbook_before_text, self.worldbook_budget
        )
        wb_body, wb_cut = self._fit_text(worldbook_text, self.worldbook_budget)
        skills_body, skills_cut = self._fit_text(skills_text, self.skills_budget)
        # 感知：默认空串（无感知源），_fit_text 直接原样返回，零开销
        perception_body, perception_cut = self._fit_text(
            perception_text, self.perception_budget
        )
        knowledge_lines, knowledge_cut = self._fit_lines(
            list(knowledge_lines or []), self.knowledge_budget
        )
        warm_lines, warm_cut = self._fit_lines(list(warm_lines or []), self.memory_budget)
        fact_lines, fact_cut = self._fit_lines(list(fact_lines or []), self.memory_budget)
        history, history_cut = self._fit_history(history, self.history_budget)
        style_text, style_cut = self._fit_text(style_text, self.style_budget)
        # 主动开口：非主动轮是空串，零开销
        proactive_text, proactive_cut = self._fit_text(
            proactive_text, self.proactive_budget
        )
        # 叙事框架：默认关闭时是空串，_fit_text 直接原样返回，零开销
        jailbreak_text, jailbreak_cut = self._fit_text(
            jailbreak_text, self.jailbreak_budget
        )

        layers: dict[str, PromptLayer] = {
            LAYER_JAILBREAK: PromptLayer(
                key=LAYER_JAILBREAK, title=JAILBREAK_SECTION, body=jailbreak_text,
                priority=_LAYER_PRIORITY[LAYER_JAILBREAK], truncated=jailbreak_cut,
            ),
            LAYER_WORLDBOOK_BEFORE: PromptLayer(
                key=LAYER_WORLDBOOK_BEFORE, title=WORLDBOOK_BEFORE_SECTION,
                body=wb_before_body,
                priority=_LAYER_PRIORITY[LAYER_WORLDBOOK_BEFORE], truncated=wb_before_cut,
            ),
            LAYER_PERSONA: PromptLayer(
                key=LAYER_PERSONA, title=ROLE_DEF_SECTION, body=persona_text,
                priority=_LAYER_PRIORITY[LAYER_PERSONA], mandatory=True,
            ),
            LAYER_WORLDBOOK: PromptLayer(
                key=LAYER_WORLDBOOK, title=WORLDBOOK_SECTION, body=wb_body,
                priority=_LAYER_PRIORITY[LAYER_WORLDBOOK], truncated=wb_cut,
            ),
            LAYER_PERCEPTION: PromptLayer(
                key=LAYER_PERCEPTION, title=PERCEPTION_SECTION, body=perception_body,
                priority=_LAYER_PRIORITY[LAYER_PERCEPTION], truncated=perception_cut,
            ),
            LAYER_SKILLS: PromptLayer(
                key=LAYER_SKILLS, title=SKILLS_SECTION, body=skills_body,
                priority=_LAYER_PRIORITY[LAYER_SKILLS], truncated=skills_cut,
            ),
            LAYER_KNOWLEDGE: PromptLayer(
                key=LAYER_KNOWLEDGE, title="参考资料：",
                body="\n".join(f"- {x}" for x in knowledge_lines),
                priority=_LAYER_PRIORITY[LAYER_KNOWLEDGE], truncated=knowledge_cut,
            ),
            LAYER_WARM: PromptLayer(
                key=LAYER_WARM, title="相关回忆：",
                body="\n".join(f"- {x}" for x in warm_lines),
                priority=_LAYER_PRIORITY[LAYER_WARM], truncated=warm_cut,
            ),
            LAYER_FACTS: PromptLayer(
                key=LAYER_FACTS, title="已知事实：",
                body="\n".join(f"- {x}" for x in fact_lines),
                priority=_LAYER_PRIORITY[LAYER_FACTS], truncated=fact_cut,
            ),
            LAYER_HISTORY: PromptLayer(
                key=LAYER_HISTORY, title="对话历史",
                body="\n".join(f"{t.role}: {t.text}" for t in history),
                priority=_LAYER_PRIORITY[LAYER_HISTORY], truncated=history_cut,
            ),
            LAYER_USER: PromptLayer(
                key=LAYER_USER, title="本次输入", body=user_input,
                priority=_LAYER_PRIORITY[LAYER_USER], mandatory=True,
            ),
            LAYER_STYLE: PromptLayer(
                key=LAYER_STYLE, title="表达风格", body=style_text,
                priority=_LAYER_PRIORITY[LAYER_STYLE], truncated=style_cut,
            ),
            LAYER_PROACTIVE: PromptLayer(
                key=LAYER_PROACTIVE, title="主动开口", body=proactive_text,
                priority=_LAYER_PRIORITY[LAYER_PROACTIVE], truncated=proactive_cut,
            ),
            LAYER_FORMAT: PromptLayer(
                key=LAYER_FORMAT, title=FORMAT_SECTION, body=self.format_text(),
                priority=_LAYER_PRIORITY[LAYER_FORMAT],
            ),
        }

        dropped: list[str] = []
        for layer in layers.values():
            layer.tokens = estimate_tokens(layer.body)
        # 历史层 token 以消息列表为准（与 layer.body 等价，但裁剪时需同步维护）
        layers[LAYER_HISTORY].tokens = sum(estimate_tokens(t.text) for t in history)

        # ---- ② 总量预算：从最低优先级可裁层开始削减 ----
        #
        # 回复格式层**不计入**总量预算：它是几十个字的固定输出约束，不是内容。
        # 计进去会让「预算刚好够」的配置连带把世界书 / 风格层裁掉——
        # 用户只是想让回复短一点，结果连设定都丢了，这是最难查的一类副作用。
        def _total() -> int:
            return sum(
                layer.tokens
                for key, layer in layers.items()
                if key != LAYER_FORMAT
            )

        # 2a) 先削减历史（丢弃最旧消息）
        while _total() > self.total_budget and history:
            history.pop(0)
            layers[LAYER_HISTORY].tokens = sum(estimate_tokens(t.text) for t in history)
            layers[LAYER_HISTORY].truncated = True
        # 2b) 再按优先级从低到高裁剪（事实 → 召回 → 个人记忆 → 技能 → 感知 →
        #     世界书 → 风格 → 叙事框架 → 主动开口）
        #     叙事框架与主动开口放最后：两者都是**本轮显式发生的事**，
        #     被裁掉会同时记一条 warning——否则「主动开口却答非所问」
        #     会变成最难查的一类问题（模型把系统触发当成用户说的话）。
        for key in (LAYER_FACTS, LAYER_WARM, LAYER_KNOWLEDGE, LAYER_SKILLS,
                    LAYER_PERCEPTION, LAYER_WORLDBOOK, LAYER_WORLDBOOK_BEFORE,
                    LAYER_STYLE, LAYER_JAILBREAK, LAYER_PROACTIVE):
            if _total() <= self.total_budget:
                break
            layer = layers[key]
            if layer.empty:
                continue
            layer.body = ""
            layer.tokens = 0
            layer.truncated = True
            dropped.append(key)
            warnings.append(f"总量预算不足，已裁减「{layer.title or key}」层")
            if key == LAYER_STYLE:
                kept_examples = []  # 风格层被裁时，示例对话一并舍弃（保持一致）

        # 3) 历史层文本按裁剪后重建
        layers[LAYER_HISTORY].body = "\n".join(f"{t.role}: {t.text}" for t in history)

        # ---- ③ 拼装 system prompt（风格块放最末：越靠近输入影响越强） ----
        sections: list[str] = []
        # 叙事框架（可选层）：放**最前**。它是「这次对话处在什么框架里」的声明，
        # 必须先于人设与设定出现，后面的内容才会被模型放在这个框架下解读。
        # 关闭时 body 为空，这一段完全不产生字符——组装结果与没有本层时逐字一致。
        # 块内自带标题（预设的 instruction_block 产出，与文风块同约定），此处不再加头。
        if not layers[LAYER_JAILBREAK].empty:
            sections.append(layers[LAYER_JAILBREAK].body)
        # 人设前的设定（ST 的 worldInfoBefore 语义）
        if not layers[LAYER_WORLDBOOK_BEFORE].empty:
            sections.append(
                f"[{WORLDBOOK_BEFORE_SECTION}]\n{layers[LAYER_WORLDBOOK_BEFORE].body}"
            )
        sections.append(f"[{ROLE_DEF_SECTION}]\n{layers[LAYER_PERSONA].body}")
        if not layers[LAYER_WORLDBOOK].empty:
            sections.append(f"[{WORLDBOOK_SECTION}]\n{layers[LAYER_WORLDBOOK].body}")
        # 感知·此刻：排在世界书之后、技能与记忆之前（理由见 _LAYER_PRIORITY 注释）。
        # 无感知源时 body 为空，这一段完全不产生字符——组装结果与不装感知时逐字一致。
        if not layers[LAYER_PERCEPTION].empty:
            sections.append(
                f"[{PERCEPTION_SECTION}]\n{layers[LAYER_PERCEPTION].body}"
            )
        if not layers[LAYER_SKILLS].empty:
            sections.append(f"[{SKILLS_SECTION}]\n{layers[LAYER_SKILLS].body}")
        if not layers[LAYER_KNOWLEDGE].empty:
            sections.append(f"[{KNOWLEDGE_SECTION}]\n{layers[LAYER_KNOWLEDGE].body}")
        memory_block = _render_memory_block(layers)
        if memory_block:
            sections.append(memory_block)
        if not layers[LAYER_STYLE].empty:
            sections.append(layers[LAYER_STYLE].body)
        # 主动开口的行为约束：放**回复格式之前**——它要说明的是「这一轮是什么情况」，
        # 与格式约束同属「对本轮的交代」，连在一起读最自然。
        # 块内自带标题（与文风块同约定），此处不再加头。非主动轮为空串，零开销。
        if not layers[LAYER_PROACTIVE].empty:
            sections.append(layers[LAYER_PROACTIVE].body)
        # 回复格式放**最末**：system 里越靠近输入的那句影响越强，而这两条约束
        # 正是要压住「模型写满额度、自己补旁白」的倾向（风格块在它之前，不会被它顶掉）。
        if not layers[LAYER_FORMAT].empty:
            sections.append(f"【{FORMAT_SECTION}】\n{layers[LAYER_FORMAT].body}")
        system_prompt = "\n\n".join(sections)

        # ---- ④ 组装 messages：示例对话（few-shot）插在历史之前 ----
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for user_text, assistant_text in kept_examples:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
        messages.extend({"role": t.role, "content": t.text} for t in history)
        messages.append({"role": "user", "content": user_input})
        # at_depth 档按深度插进对话历史（depth 从末尾往回数，0 = 最后一条之后）
        if worldbook_depth:
            messages = insert_at_depth(messages, worldbook_depth)

        total_tokens = (
            estimate_tokens(system_prompt)
            + sum(estimate_tokens(t.text) for t in history)
            + sum(estimate_tokens(u) + estimate_tokens(a) for u, a in kept_examples)
            + estimate_tokens(user_input)
        )

        return BuiltPrompt(
            system_prompt=system_prompt,
            messages=messages,
            layers=layers,
            history=history,
            total_tokens=total_tokens,
            warnings=warnings,
            dropped_layers=dropped,
            example_count=len(kept_examples),
        )
