"""LangGraph 编排：把一轮对话串成可测试、可视化的节点图。

     START
       ↓
   load_persona          渲染人设（状态变量替换）
       ↓
   worldbook_recall      世界书三通道触发 + 注入编排
       ↓
   knowledge_recall      个人记忆混合检索（BM25 + 向量 → RRF）
       ↓
   memory_recall         情景记忆混合召回 + 语义事实
       ↓
   assemble_prompt       PromptManager 分层组装（固定顺序 + 预算）
       ↓
   generate_reply        LLM 生成回复（含情绪与工具循环）
       ↓
      END

设计取舍：
- 图为**无状态纯编排**：会话读写由 chat 路由负责，节点只做计算；
- 依赖（人设/世界书/记忆/LLM）经 ChatNodes 注入，便于测试与替换；
- **记忆写入不在图内**：抽取（尤其 LLM 版）需要 1–3 秒，放在图里会让用户
  等待；现由 chat 路由在响应发出后后台执行（见 api/chat.py 的 _write_memory_job）。
"""

import time
from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import ChatNodes
from app.graph.state import ChatState

# 节点名（与 ChatNodes 方法一一对应）
NODE_LOAD_PERSONA = "load_persona"
NODE_WORLDBOOK = "worldbook_recall"
NODE_KNOWLEDGE = "knowledge_recall"
NODE_MEMORY = "memory_recall"
NODE_ASSEMBLE = "assemble_prompt"
NODE_GENERATE = "generate_reply"

# 节点执行顺序
NODE_SEQUENCE = [
    NODE_LOAD_PERSONA,
    NODE_WORLDBOOK,
    NODE_KNOWLEDGE,
    NODE_MEMORY,
    NODE_ASSEMBLE,
    NODE_GENERATE,
]


def _timed(name: str, node: Callable[[ChatState], dict]) -> Callable[[ChatState], dict]:
    """给节点包一层耗时统计，把毫秒数累加进 `state["timings"]`。

    为什么包在**图上**而不是写进节点：节点是纯函数，会被测试与脚本直接调用，
    计时属于编排层关注点；写进每个节点既污染语义，也容易漏。
    图是唯一的执行入口，包一层就全覆盖。

    累加方式：入口读当前 state 里的 timings（LangGraph 已把上游节点的结果合并
    进来），追加自己这一项后随返回值一起回写。节点是串行执行的，因此不会互相覆盖。
    """
    def wrapped(state: ChatState) -> dict:
        started = time.perf_counter()
        update = node(state) or {}
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        timings = dict(state.get("timings") or {})
        timings[name] = round(elapsed_ms, 1)
        return {**update, "timings": timings}

    wrapped.__name__ = f"timed_{name}"
    return wrapped


def build_chat_graph(nodes: ChatNodes):
    """编译对话编排图（返回可直接 invoke 的 CompiledStateGraph）。"""
    builder = StateGraph(ChatState)

    builder.add_node(NODE_LOAD_PERSONA, _timed(NODE_LOAD_PERSONA, nodes.load_persona))
    builder.add_node(NODE_WORLDBOOK, _timed(NODE_WORLDBOOK, nodes.worldbook_recall))
    builder.add_node(NODE_KNOWLEDGE, _timed(NODE_KNOWLEDGE, nodes.knowledge_recall))
    builder.add_node(NODE_MEMORY, _timed(NODE_MEMORY, nodes.memory_recall))
    builder.add_node(NODE_ASSEMBLE, _timed(NODE_ASSEMBLE, nodes.assemble_prompt))
    builder.add_node(NODE_GENERATE, _timed(NODE_GENERATE, nodes.generate_reply))

    builder.add_edge(START, NODE_LOAD_PERSONA)
    for current, nxt in zip(NODE_SEQUENCE, NODE_SEQUENCE[1:]):
        builder.add_edge(current, nxt)
    builder.add_edge(NODE_GENERATE, END)

    return builder.compile()
