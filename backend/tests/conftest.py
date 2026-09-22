"""pytest 全局配置：测试隔离。

关键约束：**测试结果不得依赖本地 backend/.env 状态**，也不得发起真实网络请求。
- 记忆门面 → 临时 SQLite + 内存温层；
- embedding → 强制本地确定性实现（连 API 与本地 .env 配置都不看）；
- LLM provider → 强制 mock（即使 .env 配了真实 key，常规测试也不联网）；
- 数字人 provider → 强制本地降级（不调用魔珐 TTS）；
- MCP → 强制关闭（不拉起任何 MCP 子进程；端到端测试见 tests/test_mcp/test_integration.py）；
- 需要真实模型的联调测试见 tests/test_real_llm_smoke.py（默认 skip）。
"""

import pytest

from app.api import chat as chat_module
from app.api import knowledge as knowledge_module
from app.api import llm as llm_module
from app.api import media as media_module
from app.digital_human.local_provider import LocalDigitalHumanProvider
from app.llm.mock import MockLLMProvider
from app.mcp.manager import McpManager, set_mcp_manager
from app.memory.cold.mood_log import SqliteMoodLogStore
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.store import MemoryStore
from app.memory.warm.embedding import DeterministicEmbeddingProvider
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.prompts.st_compat import StPresetStore
from app.session.sqlite_repository import SqliteSessionRepository


@pytest.fixture(autouse=True)
def isolated_chat_dependencies(tmp_path, monkeypatch):
    """自动替换 chat/media 模块的外部依赖为隔离实现。"""
    # MCP 默认关闭：环境变量优先于 .env，即使本地配了清单也不会连
    monkeypatch.setenv("MCP_ENABLED", "false")
    # 同时注入空管理器：graph 构建时不会注册任何 MCP 工具
    set_mcp_manager(McpManager())

    store = MemoryStore(
        SqliteColdStore(db_path=tmp_path / "memory.db"),
        InMemoryWarmStore(),
    )
    # 顺序要紧：set_embedding_provider 会连带重建记忆门面，须在 set_memory_store 之前
    chat_module.set_embedding_provider(DeterministicEmbeddingProvider())
    chat_module.set_memory_store(store)
    knowledge_module.set_knowledge_store(InMemoryKnowledgeStore())
    chat_module.set_mood_store(SqliteMoodLogStore(db_path=tmp_path / "mood.db"))
    # 会话存储：用临时 SQLite（而非默认 backend/data/memory.db），
    # 同时让 API 测试真实覆盖持久化实现（而非内存版）
    chat_module.set_session_repository(
        SqliteSessionRepository(tmp_path / "sessions.db")
    )
    chat_module.set_llm_provider(MockLLMProvider())
    media_module.set_digital_human_provider(LocalDigitalHumanProvider())
    # ST 预设存储：指向临时目录（绝不碰 backend/data/presets，也不依赖本机状态）
    chat_module.set_st_preset_store(StPresetStore(tmp_path / "st_presets"))
    # 模型运行时配置：指向临时文件 + 清空进程内缓存
    # （绝不碰 backend/data/llm_runtime.json，也不受本机已保存的模型切换影响）
    monkeypatch.setenv("LLM_RUNTIME_PATH", str(tmp_path / "llm_runtime.json"))
    # 同时把**部署配置**固定成 mock：测试结果不得依赖本机 backend/.env
    # （本机可能配了真实 key，会让“默认回落 .env”类断言闯到真实 provider 上）
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    llm_module.reset_llm_config_cache()
    yield store
    chat_module.set_embedding_provider(None)
    chat_module.set_memory_store(None)
    knowledge_module.set_knowledge_store(None)
    chat_module.set_mood_store(None)
    chat_module.set_session_repository(None)
    chat_module.set_llm_provider(None)
    chat_module.set_st_preset_store(None)
    llm_module.reset_llm_config_cache()
    media_module.set_digital_human_provider(None)
    set_mcp_manager(None)
