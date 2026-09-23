"""内置插件收编测试：既有扩展点被正确包装且行为不变。"""

import pytest

from app.plugins.builtin import build_builtin_registrations, register_all_builtin
from app.plugins.capabilities import CapabilityType, PluginLayer
from app.plugins.manifest import PluginManifest
from app.plugins.registry import PluginRegistry

EXPECTED_IDS = [
    "llm-providers",
    "avatar-providers",
    "tts",
    "embedding",
    "memory-warm",
    "memory-knowledge",
    "session-store",
    "tools-builtin",
    "mcp-bridge",
    # P2（§9.11）拆分后新收编的五项
    "tokenizer",
    "knowledge-parser",
    "knowledge-chunker",
    "preset-ai-adaptation",
]


def test_all_builtin_points_are_registered() -> None:
    registry = PluginRegistry()
    ids = register_all_builtin(registry)
    assert ids == EXPECTED_IDS
    assert len(registry) == len(EXPECTED_IDS)


def test_all_builtin_are_builtin_layer() -> None:
    """收编的既有实现属于 builtin 层（可禁用/替换），不是 core。"""
    for registration in build_builtin_registrations():
        assert registration.manifest.layer is PluginLayer.BUILTIN
        assert registration.manifest.is_core is False


def test_manifests_have_display_name_and_category() -> None:
    """管理 UI 依赖展示名与分类，二者不可为空。"""
    for registration in build_builtin_registrations():
        manifest = registration.manifest
        assert isinstance(manifest, PluginManifest)
        assert manifest.display_name.strip()
        assert manifest.category.strip()


def test_llm_provider_via_registry() -> None:
    """经注册表取 LLM 实现——行为与直接调用工厂一致。"""
    registry = PluginRegistry()
    register_all_builtin(registry)

    provider = registry.provider("llm", "mock")
    assert provider.name == "mock"

    # 未知实现名仍由原工厂报错（错误信息不丢）
    try:
        registry.provider("llm", "not-a-provider")
    except ValueError as exc:
        assert "未知 LLM provider" in str(exc)
    else:  # pragma: no cover - 防御性
        raise AssertionError("未知 provider 应抛出 ValueError")


def test_session_store_adapter_aligns_signature(tmp_path) -> None:
    """session 工厂的 db_path 是位置参数，适配后仍可正常创建。"""
    registry = PluginRegistry()
    register_all_builtin(registry)

    store = registry.provider("session_store", "memory")
    assert store is not None

    db = tmp_path / "sessions.db"
    sqlite_store = registry.provider("session_store", "sqlite", db_path=str(db))
    assert sqlite_store is not None


@pytest.fixture
def no_skills(tmp_path):
    """把全局技能库置空。

    `_builtin_tools()` 会调 `build_default_registry()`，而后者读全局技能库：
    技能非空时工具集会多一个 `study_skill`，直接断言「恰好 4 个」
    就会随仓库里放了几个技能文件而变。
    """
    from app.skills.registry import SkillRegistry, get_skill_registry, set_skill_registry

    original = get_skill_registry()
    set_skill_registry(SkillRegistry(state_path=tmp_path / "skills.json"))
    try:
        yield
    finally:
        set_skill_registry(original)


def test_tools_builtin_contributes_four_tools(no_skills) -> None:
    registry = PluginRegistry()
    register_all_builtin(registry)

    names = sorted(spec.name for spec in registry.tools())
    assert names == [
        "query_mood_trend",
        "recall_memory",
        "record_mood_journal",
        "start_breathing_exercise",
    ]


def test_datasource_capability_not_yet_provided() -> None:
    """`datasource` 能力留给 tavern-bridge（P3）；此刻应无提供者。"""
    registry = PluginRegistry()
    register_all_builtin(registry)

    assert "datasource" not in registry.capabilities_summary()
    assert CapabilityType.DATASOURCE.value == "datasource"


# ---------- P2 拆分后新收编的两项 ----------


def test_tokenizer_via_registry() -> None:
    """经注册表取分词器——既有的 `tokenize()` 模块函数与它结果一致。"""
    from app.rag.retrieval.tokenize import create_tokenizer, tokenize

    registry = PluginRegistry()
    register_all_builtin(registry)

    assert registry.provider("tokenizer", "char-bigram").name == "char-bigram"
    # 空实现名 → auto（跟随环境）
    assert registry.provider("tokenizer").name == create_tokenizer("auto").name
    # 能力可替换但不改变既有调用方的行为
    text = "最近压力有点大"
    assert tokenize(text) == create_tokenizer("auto").tokenize(text)

    try:
        registry.provider("tokenizer", "bert")
    except ValueError as exc:
        assert "未知分词器后端" in str(exc)
    else:  # pragma: no cover - 防御性
        raise AssertionError("未知分词器应抛出 ValueError")


def test_parser_via_registry() -> None:
    """parser 的能力面分两层：按格式取实现，或取自动选择的入口。"""
    registry = PluginRegistry()
    register_all_builtin(registry)

    pdf = registry.provider("parser", "pdf")
    assert pdf.name == "pdf"
    assert ".pdf" in pdf.suffixes

    # 空实现名 → 自动选择的 parse 入口（既有多数调用方的用法）
    auto = registry.provider("parser")
    result = auto("笔记.txt", "你好".encode("utf-8"))
    assert result.source_type == "text"

    try:
        registry.provider("parser", "rtf")
    except ValueError as exc:
        assert "未知文档格式" in str(exc)
    else:  # pragma: no cover - 防御性
        raise AssertionError("未知格式应抛出 ValueError")


def test_chunker_via_registry() -> None:
    """经注册表取分块器——既有 `split_text()` 与它结果一致。"""
    from app.memory.knowledge.chunker import create_chunker, split_text

    registry = PluginRegistry()
    register_all_builtin(registry)

    assert registry.provider("chunker", "plain").name == "plain"
    # 空实现名 → auto：没给 embed 就是 plain
    assert registry.provider("chunker").name == "plain"

    text = "第一段。\n\n第二段。"
    assert split_text(text) == create_chunker("auto").split(text)

    try:
        registry.provider("chunker", "by-topic")
    except ValueError as exc:
        assert "未知分块器" in str(exc)
    else:  # pragma: no cover - 防御性
        raise AssertionError("未知分块器应抛出 ValueError")


def test_tts_via_registry() -> None:
    """经注册表取语音合成实现（§9.10 第 12 项，从 digital_human 分出）。"""
    from app.tts.base import TtsNotAvailable

    registry = PluginRegistry()
    register_all_builtin(registry)

    assert registry.provider("tts", "gpt_sovits").name == "gpt_sovits"
    # 空实现名 → none（不做合成，前端回落浏览器 TTS）
    assert registry.provider("tts").name == "none"
    assert registry.provider("tts").available() is False

    with pytest.raises(TtsNotAvailable):
        registry.provider("tts").synthesize("你好")
    with pytest.raises(ValueError, match="未知 TTS 实现"):
        registry.provider("tts", "edge")


def test_preset_adaptation_via_registry() -> None:
    """经注册表取预设适配能力。

    这个能力按**动作名**寻址而不是实现名——适配天然是一组动作，
    不是若干可换的实现（见 `_preset_adaptation` 的说明）。
    """
    from app.prompts.adaptation import detect as raw_detect

    registry = PluginRegistry()
    register_all_builtin(registry)

    assert registry.provider("preset_adaptation", "detect") is raw_detect

    group = registry.provider("preset_adaptation")
    assert set(group) == {"detect", "plan", "build_diff", "load_rules", "load_rules_file"}

    try:
        registry.provider("preset_adaptation", "rewrite")
    except ValueError as exc:
        assert "未知适配动作" in str(exc)
    else:  # pragma: no cover - 防御性
        raise AssertionError("未知适配动作应抛出 ValueError")


def test_disabling_split_plugins_does_not_break_retrieval() -> None:
    """可禁用性验证：禁用分词/解析插件不影响模块级入口（它们不经过注册表）。

    这一点很重要——注册表是**能力账本**，不是运行时唯一通路；否则禁用插件
    会把检索链路直接打断（而 §9.2 说 builtin 层是可禁用的）。
    """
    from app.memory.knowledge.parser import parse
    from app.rag.retrieval.tokenize import tokenize

    registry = PluginRegistry()
    register_all_builtin(registry)

    assert registry.provider("tokenizer") is not None
    registry.set_enabled("tokenizer", False)
    registry.set_enabled("knowledge-parser", False)

    # 能力账本里不再有提供者
    with pytest.raises(KeyError, match="没有启用的插件提供能力"):
        registry.provider("tokenizer")

    # 但既有调用方不经注册表，照常工作
    assert tokenize("压力大")
    assert parse("a.txt", b"hi").source_type == "text"


# ---------- embedding（§9.10 第 8 项）----------


def test_embedding_via_registry() -> None:
    """经注册表取向量化实现。

    这一项原本就不缺接口与工厂，缺的只是**注册**——`/health` 的能力索引里
    看不到 `embedding`，尽管评审可以在 .env 里配 `EMBEDDING_PROVIDER=dashscope`。
    """
    registry = PluginRegistry()
    register_all_builtin(registry)

    provider = registry.provider("embedding", "deterministic")
    assert provider.dimension > 0
    assert len(provider.embed("测试文本")) == provider.dimension

    # 空实现名 → deterministic（零依赖默认）
    assert registry.provider("embedding").dimension > 0

    try:
        registry.provider("embedding", "weird")
    except ValueError as exc:
        assert "未知 embedding provider" in str(exc)
    else:  # pragma: no cover - 防御性
        raise AssertionError("未知 embedding provider 应抛出 ValueError")


def test_disabling_builtin_removes_its_tools(no_skills) -> None:
    """可禁用性验证：禁用工具插件后，聚合工具列表为空。"""
    registry = PluginRegistry()
    register_all_builtin(registry)
    assert len(registry.tools()) == 4

    registry.set_enabled("tools-builtin", False)
    assert registry.tools() == []
