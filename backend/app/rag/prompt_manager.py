"""PromptManager：分层提示词组装 + 优先级预算管理（M3 正式实现）。

设计参照①的固定组装顺序与优先级（高 → 低）：

    人设 > 世界书命中 > 向量召回 > 结构化事实 > 滚动窗口 > 本次输入

预算策略（两级）：
1. 层内预算：世界书块、记忆块、历史窗口各自独立预算，超出即截断；
2. 总量预算：总和超过 total_budget 时，从**最低优先级层**开始裁减
   （历史窗口先裁最旧消息 → 记忆块尾部 → 世界书尾部），
   人设与本次输入为必留层（mandatory），永不裁剪。

输出 BuiltPrompt：可直接转 OpenAI 风格 messages，并带各层 token 统计，
供 LangGraph 节点与调试使用。
"""

from dataclasses import dataclass, field

from app.prompts.renderer import estimate_tokens
from app.session.context import ChatTurn

# ---- 层标识 ----
LAYER_PERSONA = "persona"        # 角色人设（必留）
LAYER_WORLDBOOK = "worldbook"    # 世界书命中
LAYER_WARM = "warm_recall"       # 温层向量召回
LAYER_FACTS = "cold_facts"       # 冷层结构化事实
LAYER_HISTORY = "history"        # 滚动窗口（可裁最旧）
LAYER_USER = "user_input"        # 本次输入（必留）
LAYER_STYLE = "style"            # 表达风格（M5，放 system 末尾：越靠近输入影响越强）

# ---- 段落标题 ----
ROLE_DEF_SECTION = "角色人设"
WORLDBOOK_SECTION = "场景补充"
MEMORY_SECTION = "记忆回忆"

# ---- 默认预算 ----
DEFAULT_WORLDBOOK_BUDGET = 400
DEFAULT_MEMORY_BUDGET = 400
DEFAULT_HISTORY_BUDGET = 700
DEFAULT_TOTAL_BUDGET = 4000
DEFAULT_STYLE_BUDGET = 400     # 风格块预算（含示例对话）

# 层优先级（数字越大越重要，裁剪时从最小开始）
_LAYER_PRIORITY = {
    LAYER_PERSONA: 100,
    LAYER_USER: 100,
    LAYER_STYLE: 90,      # 表达风格很重要，但在总量不足时仍可裁（排在必留层之后）
    LAYER_WORLDBOOK: 80,
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
        memory_budget: int = DEFAULT_MEMORY_BUDGET,
        history_budget: int = DEFAULT_HISTORY_BUDGET,
        total_budget: int = DEFAULT_TOTAL_BUDGET,
        style_budget: int = DEFAULT_STYLE_BUDGET,
    ) -> None:
        self.worldbook_budget = worldbook_budget
        self.memory_budget = memory_budget
        self.history_budget = history_budget
        self.total_budget = total_budget
        self.style_budget = style_budget

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

    def build(
        self,
        *,
        persona_text: str,
        user_input: str,
        worldbook_text: str = "",
        warm_lines: list[str] | None = None,
        fact_lines: list[str] | None = None,
        history: list[ChatTurn] | None = None,
        style_text: str = "",
        examples: list[tuple[str, str]] | None = None,
    ) -> BuiltPrompt:
        """组装完整提示。

        参数均为**已渲染好**的各层内容（召回与变量替换由调用方完成），
        本方法只负责分层编排、预算与裁剪。

        参数:
            style_text: 表达风格指令块，放在 **system 末尾**（越靠近输入影响越强）；
            examples: 示例对话（few-shot），以真实消息对插在历史之前。
        """
        warnings: list[str] = []
        history = list(history or [])
        examples = list(examples or [])

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
        wb_body, wb_cut = self._fit_text(worldbook_text, self.worldbook_budget)
        warm_lines, warm_cut = self._fit_lines(list(warm_lines or []), self.memory_budget)
        fact_lines, fact_cut = self._fit_lines(list(fact_lines or []), self.memory_budget)
        history, history_cut = self._fit_history(history, self.history_budget)
        style_text, style_cut = self._fit_text(style_text, self.style_budget)

        layers: dict[str, PromptLayer] = {
            LAYER_PERSONA: PromptLayer(
                key=LAYER_PERSONA, title=ROLE_DEF_SECTION, body=persona_text,
                priority=_LAYER_PRIORITY[LAYER_PERSONA], mandatory=True,
            ),
            LAYER_WORLDBOOK: PromptLayer(
                key=LAYER_WORLDBOOK, title=WORLDBOOK_SECTION, body=wb_body,
                priority=_LAYER_PRIORITY[LAYER_WORLDBOOK], truncated=wb_cut,
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
        }

        dropped: list[str] = []
        for layer in layers.values():
            layer.tokens = estimate_tokens(layer.body)
        # 历史层 token 以消息列表为准（与 layer.body 等价，但裁剪时需同步维护）
        layers[LAYER_HISTORY].tokens = sum(estimate_tokens(t.text) for t in history)

        # ---- ② 总量预算：从最低优先级可裁层开始削减 ----
        def _total() -> int:
            return sum(layer.tokens for layer in layers.values())

        # 2a) 先削减历史（丢弃最旧消息）
        while _total() > self.total_budget and history:
            history.pop(0)
            layers[LAYER_HISTORY].tokens = sum(estimate_tokens(t.text) for t in history)
            layers[LAYER_HISTORY].truncated = True
        # 2b) 再按优先级从低到高裁剪（摘要 → 事实 → 召回 → 世界书 → 风格）
        for key in (LAYER_FACTS, LAYER_WARM, LAYER_WORLDBOOK, LAYER_STYLE):
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
        sections: list[str] = [f"[{ROLE_DEF_SECTION}]\n{layers[LAYER_PERSONA].body}"]
        if not layers[LAYER_WORLDBOOK].empty:
            sections.append(f"[{WORLDBOOK_SECTION}]\n{layers[LAYER_WORLDBOOK].body}")
        memory_block = _render_memory_block(layers)
        if memory_block:
            sections.append(memory_block)
        if not layers[LAYER_STYLE].empty:
            sections.append(layers[LAYER_STYLE].body)
        system_prompt = "\n\n".join(sections)

        # ---- ④ 组装 messages：示例对话（few-shot）插在历史之前 ----
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for user_text, assistant_text in kept_examples:
            messages.append({"role": "user", "content": user_text})
            messages.append({"role": "assistant", "content": assistant_text})
        messages.extend({"role": t.role, "content": t.text} for t in history)
        messages.append({"role": "user", "content": user_input})

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
