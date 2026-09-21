"""真实 LLM 联调冒烟测试（默认跳过，需显式开启）。

用途：接入真实云端模型后验证「配置 → provider → 回复」链路可用。

开启方式（在 backend 目录）：
    1) 编辑 backend/.env，填入真实 provider 与 key，例如：
         LLM_PROVIDER=dashscope
         LLM_API_KEY=sk-你的key
         LLM_MODEL=qwen2.5-7b-instruct
    2) 运行：
         HYPRA_REAL_LLM=1 ../.venv/Scripts/python.exe -m pytest tests/test_real_llm_smoke.py -v -s

注意：本文件不会在常规测试中执行（默认 skip），也不会读取或打印你的 key。
"""

import os

import pytest

from app.config import get_settings
from app.llm.base import ChatMessage
from app.llm.factory import create_llm_provider

pytestmark = pytest.mark.skipif(
    os.getenv("HYPRA_REAL_LLM") != "1",
    reason="需真实 key：设置环境变量 HYPRA_REAL_LLM=1 后运行",
)


def _real_provider():
    """按 .env 配置创建真实 provider（mock 则跳过）。"""
    settings = get_settings()
    if settings.llm_provider == "mock":
        pytest.skip("当前 LLM_PROVIDER=mock：请在 backend/.env 配置真实 provider 与 key")
    return create_llm_provider(
        settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
    )


def test_real_llm_returns_reply() -> None:
    """真实模型应返回非空回复。"""
    provider = _real_provider()
    reply = provider.chat(
        [ChatMessage(role="user", content="你好，我最近工作有点累。")]
    )
    print(f"\n[provider={provider.name} model={provider.model}] 回复：{reply}")
    assert reply.strip(), "真实模型返回了空回复"


def test_real_llm_follows_persona_prompt() -> None:
    """带人设 system prompt 时，回复应体现温柔倾听基调（弱断言）。"""
    provider = _real_provider()
    system = (
        "你是苏澄，三十四岁的女性心理咨询师，温柔体贴、善解人意。"
        "先接住对方的情绪，再温和地引导，不说教。"
    )
    reply = provider.chat(
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content="我妈最近身体不好，我很担心。"),
        ]
    )
    print(f"\n[人设联调] 回复：{reply}")
    assert len(reply.strip()) > 5


def test_real_llm_full_graph_flow() -> None:
    """端到端：LangGraph 编排 + 真实 LLM + 记忆写入。"""
    from app.graph.chat_graph import build_chat_graph
    from app.graph.nodes import ChatNodes
    from app.memory.cold.sqlite_store import SqliteColdStore
    from app.memory.store import MemoryStore
    from app.memory.warm.inmemory_store import InMemoryWarmStore
    from app.prompts.persona.loader import load_builtin_presets
    from app.worldbook.loader import load_builtin_entries

    import tempfile
    from pathlib import Path

    provider = _real_provider()
    tmp = Path(tempfile.mkdtemp()) / "real_smoke.db"
    memory = MemoryStore(SqliteColdStore(db_path=tmp), InMemoryWarmStore())

    nodes = ChatNodes(
        presets=load_builtin_presets(),
        entries=load_builtin_entries(),
        memory_store=memory,
        llm_provider=provider,
    )
    graph = build_chat_graph(nodes)

    result = graph.invoke(
        {
            "session_id": "smoke-1",
            "companion_id": "therapist-elder-sister",
            "persona_id": "therapist-elder-sister",
            "user_name": "小林",
            "user_input": "我最近总是失眠，压力很大。",
            "history": [],
            "turn_index": 1,
            "state_vars": {"current_mood": "焦虑"},
            "warnings": [],
        }
    )
    print(f"\n[端到端] 命中世界书：{[e.id for e in result['worldbook_hits']]}")
    print(f"[端到端] 写入统计：{result['writes']}")
    print(f"[端到端] 回复：{result['reply']}")

    assert result["reply"].strip()
    assert result["writes"]["memory"] == 1
