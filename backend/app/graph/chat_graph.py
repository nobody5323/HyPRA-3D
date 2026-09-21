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


def build_chat_graph(nodes: ChatNodes):
    """编译对话编排图（返回可直接 invoke 的 CompiledStateGraph）。"""
    builder = StateGraph(ChatState)

    builder.add_node(NODE_LOAD_PERSONA, nodes.load_persona)
    builder.add_node(NODE_WORLDBOOK, nodes.worldbook_recall)
    builder.add_node(NODE_KNOWLEDGE, nodes.knowledge_recall)
    builder.add_node(NODE_MEMORY, nodes.memory_recall)
    builder.add_node(NODE_ASSEMBLE, nodes.assemble_prompt)
    builder.add_node(NODE_GENERATE, nodes.generate_reply)

    builder.add_edge(START, NODE_LOAD_PERSONA)
    for current, nxt in zip(NODE_SEQUENCE, NODE_SEQUENCE[1:]):
        builder.add_edge(current, nxt)
    builder.add_edge(NODE_GENERATE, END)

    return builder.compile()
