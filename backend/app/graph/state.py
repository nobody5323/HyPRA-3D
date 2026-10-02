"""LangGraph 编排状态定义。

ChatState 是一轮对话在图中的流转载体：输入 → 中间产物 → 输出。
字段均可选（total=False），节点各自填充自己负责的部分。
"""

from typing import TypedDict

from app.llm.profiles import ResolvedSampling
from app.memory.store import MemoryContext
from app.prompts.assemble import DepthInjection
from app.prompts.st_compat import ParsedPreset
from app.session.context import ChatTurn
from app.tools.emotion import EmotionResult
from app.worldbook.models import WorldBookEntry


class ChatState(TypedDict, total=False):
    """一轮对话的编排状态。"""

    # ---- 输入（chat 路由填充）----
    session_id: str
    companion_id: str            # 记忆隔离命名空间（= persona_id）
    persona_id: str
    mode: str                    # 交互模式：companion（桌宠）/ tavern（酒馆）
    user_name: str
    user_input: str
    history: list[ChatTurn]      # 本次输入之前的既有轮次
    turn_index: int              # 本轮序号（摘要并入用）
    state_vars: dict[str, str]   # 动态状态变量（current_mood 等）
    style_id: str                # 本轮文风预设 id（缺省用默认档）
    # 本轮叙事框架（jailbreak）预设 id：
    #   未传/空串 = 用部署默认（jailbreak_enabled 关闭时即「关闭」）
    #   "none"   = 本轮强制关闭（覆盖部署默认）
    #   其它值    = 本轮显式指定档位，同时隐含启用（用户可以在单轮里主动开）
    jailbreak_id: str
    preset_id: str               # 本轮模型预设 id（空串 = 按模型名自动匹配）
    st_preset_id: str            # 本轮选用的 ST 预设 id（空串 = 走内置分层路径）
    st_preset: ParsedPreset | None   # 已解析（含覆盖层）的 ST 预设
    macro_variables: dict[str, str]  # 会话级宏变量（ST 的 {{setvar::}} 存储）
    # 感知·此刻（已渲染的 `- …` 行，由 app/perception/context.py 唯一产出）。
    # 由路由层填好再进图：节点不必知道感知快照这个全局单例的存在。
    perception_text: str
    # 主动轮标记。见 docs/proactive-multimodal.md §5.4——
    # 主动消息走**同一条图**，靠这个标记让提示词层追加行为约束。
    proactive: bool
    proactive_reason: str            # 触发原因（内部信息，不进用户可见文本）
    proactive_text: str              # 主动开口的行为约束块（非主动轮为空串）

    # ---- 中间产物（各节点填充）----
    persona_text: str
    jailbreak_text: str          # 本轮生效的叙事框架块（关闭时为空串）
    worldbook_hits: list[WorldBookEntry]
    worldbook_skipped: list[WorldBookEntry]
    worldbook_text: str
    worldbook_before_text: str   # before_char 档：排在人设前的「背景设定」块
    worldbook_depth: list[DepthInjection]   # at_depth 档：按 depth 插进对话历史
    knowledge_lines: list[str]   # 个人记忆（知识库）召回文本行
    memory_context: MemoryContext | None
    warm_lines: list[str]        # 情景记忆召回文本行
    fact_lines: list[str]        # 语义记忆（事实）文本行
    system_prompt: str
    messages: list[dict[str, str]]
    st_preset_meta: dict         # ST 组装元信息（markers / 未识别宏 / In-Chat 注入数）

    # ---- 输出 ----
    reply: str
    emotion: EmotionResult | None   # 本轮情绪判定（结构化输出或兜底）
    sampling: ResolvedSampling | None  # 本轮实际使用的采样参数（模型档 ⊕ 文风）
    example_count: int           # 注入的 few-shot 示例组数
    tools_used: list[dict]       # 本轮实际调用过的工具记录（Agent 行动层）
    # 本轮 LLM 实际调用次数（含工具循环与强制情绪补调）。
    # 「回复慢」的第一嫌疑永远是调用次数 × 单次耗时，必须能一眼看见。
    llm_rounds: int
    # 各节点耗时（毫秒，键为节点名）。由 build_chat_graph 的计时包装写入——
    # 不写在节点内部：节点是纯函数、会被直接调用与单测，计时属于编排层关注点。
    timings: dict[str, float]
    writes: dict[str, int]       # 已废弃：写入移至路由层后台执行，不再进入图状态
    warnings: list[str]
