"""内置插件收编测试：既有扩展点被正确包装且行为不变。"""

import pytest

from app.plugins.builtin import build_builtin_registrations, register_all_builtin
from app.plugins.capabilities import CapabilityType, PluginLayer
from app.plugins.manifest import PluginManifest
from app.plugins.registry import PluginRegistry

EXPECTED_IDS = [
    "llm-providers",
    "avatar-providers",
    "memory-warm",
    "memory-knowledge",
    "session-store",
    "tools-builtin",
    "mcp-bridge",
    # P2（§9.11）拆分后新收编的两项
    "tokenizer",
    "knowledge-parser",
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


def test_disabling_builtin_removes_its_tools(no_skills) -> None:
    """可禁用性验证：禁用工具插件后，聚合工具列表为空。"""
    registry = PluginRegistry()
    register_all_builtin(registry)
    assert len(registry.tools()) == 4

    registry.set_enabled("tools-builtin", False)
    assert registry.tools() == []
