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
from app.config import clear_runtime_overrides
from app.digital_human.local_provider import LocalDigitalHumanProvider
from app.events.bus import set_event_bus
from app.llm.base import ChatMessage, LLMProvider
from app.llm.mock import MockLLMProvider
from app.mcp.manager import McpManager, set_mcp_manager
from app.memory.cold.mood_log import SqliteMoodLogStore
from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.knowledge.inmemory_store import InMemoryKnowledgeStore
from app.memory.store import MemoryStore
from app.memory.warm.embedding import DeterministicEmbeddingProvider
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.perception.snapshot import set_perception_snapshot
from app.perception.profile import reset_profile_cache
from app.perception.timeline import ActivityTimeline, set_timeline
from app.prompts.st_compat import StPresetStore
from app.proactive.runner import set_proactive_runner
from app.proactive.state import set_state_store
from app.session.sqlite_repository import SqliteSessionRepository
from app.studio import StudioStore


@pytest.fixture(autouse=True)
def isolated_chat_dependencies(tmp_path, monkeypatch):
    """自动替换 chat/media 模块的外部依赖为隔离实现。"""
    # 运行时配置覆盖层（界面设置 > .env）是**进程级**状态：本机 backend/data 下
    # 若存过插件配置（如 data/plugins/embedding/settings.json），它会污染其它用例。
    # 每个用例前后都清空，让测试结果只取决于 .env 与环境变量。
    clear_runtime_overrides()
    # MCP 默认关闭：环境变量优先于 .env，即使本地配了清单也不会连
    monkeypatch.setenv("MCP_ENABLED", "false")
    # 数字人驱动固定为本地：测试不得依赖本机 .env（本机可能配了 gpt_sovits，
    # 启动预热会真的去连 127.0.0.1:9880 → 破坏「测试不联网」原则）
    monkeypatch.setenv("DIGITAL_HUMAN_PROVIDER", "local")
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
    # 创作工坊：**用户目录**指向临时目录（内置资源仍从包目录读——那是项目内容，
    # 不属于「本机状态」），否则测试会读到开发机上的自建角色，结果无法复现
    chat_module.set_studio_store(StudioStore(tmp_path / "studio"))
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

    # ---- 感知层与主动链路（docs/proactive-multimodal.md）----
    # 节流状态：指向临时文件。否则每轮 `POST /chat` 都会去写开发机上的
    # backend/data/proactive-state.json——测试不该有这种副作用。
    monkeypatch.setenv("PROACTIVE_STATE_PATH", str(tmp_path / "proactive-state.json"))
    # 语音识别默认关（不加载任何模型；`ASR_PROVIDER` 出厂即 none，这里显式钉住，
    # 避免本机 .env 配了 faster-whisper 时测试去加载几百 MB 权重）
    monkeypatch.setenv("ASR_PROVIDER", "none")
    # 三个进程内单例都重置：它们是跨用例的全局状态
    set_state_store(None)
    set_proactive_runner(None)
    set_perception_snapshot(None)
    set_event_bus(None)
    # 行踪与画像：**指向临时目录**。否则每轮 `POST /perception/desktop`
    # 都会往开发机的 backend/data/perception/ 里写行踪文件——
    # 测试不该有这种副作用（那正是 PROACTIVE_STATE_PATH 那条同款理由）。
    set_timeline(ActivityTimeline(root=tmp_path / "perception"))
    reset_profile_cache()
    yield store
    chat_module.set_embedding_provider(None)
    chat_module.set_memory_store(None)
    knowledge_module.set_knowledge_store(None)
    chat_module.set_mood_store(None)
    chat_module.set_session_repository(None)
    chat_module.set_llm_provider(None)
    chat_module.set_st_preset_store(None)
    chat_module.set_studio_store(None)
    llm_module.reset_llm_config_cache()
    media_module.set_digital_human_provider(None)
    set_mcp_manager(None)
    set_state_store(None)
    set_proactive_runner(None)
    set_perception_snapshot(None)
    set_event_bus(None)
    set_timeline(None)
    reset_profile_cache()
    clear_runtime_overrides()

class ScriptedLLM(LLMProvider):
    """按脚本逐次返回固定文本的对话模型替身（AI 创作的测试用）。"""

    name = "scripted"
    model = "scripted-model"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[ChatMessage]] = []

    def chat(self, messages: list[ChatMessage], **_: object) -> str:
        self.calls.append(list(messages))
        return self.replies.pop(0) if self.replies else ""


@pytest.fixture
def scripted_llm():
    """把对话模型换成按脚本返回的替身，返回安装函数。

    经 `chat.set_llm_provider` 注入——那是既有的运行时切换入口，因此测试走的是
    **真实解析路径**（包括「本地占位实现一律拒绝」那条规则），而不是绕过它。
    用例结束后由上面的 autouse fixture 恢复。
    """

    def install(replies: list[str]) -> ScriptedLLM:
        provider = ScriptedLLM(replies)
        chat_module.set_llm_provider(provider)
        return provider

    return install
