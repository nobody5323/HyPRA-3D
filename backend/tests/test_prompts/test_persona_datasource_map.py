"""数据源 → persona 的宿主侧映射测试。

夹具全部**自创**（`AGENTS.md §8.6` 红线）：不使用任何真实角色卡内容，
只复刻它们的**结构**（宏、空字段、超长正文、备用开场白、重复标签…）。
"""

from app.plugins.contracts import DataSourceCharacter, DataSourceSnapshot
from app.plugins.datasources import stable_item_id
from app.prompts.persona.datasource_map import (
    PERSONA_ID_PREFIX,
    align_macros,
    collect_datasource_personas,
    persona_id_for,
    personas_from_snapshot,
    scopes_by_character_name,
)


def _card(**overrides) -> DataSourceCharacter:
    base = {
        "name": "示例角色",
        "description": "{{char}} 是街角茶馆的老板，{{user}} 常来找 {{char}} 喝茶。",
        "personality": "温和，话不多。",
        "scenario": "老城区的茶馆。",
        "source": "demo-source",
        "source_id": "char#1",
    }
    base.update(overrides)
    return DataSourceCharacter(**base)


def _snapshot(*characters: DataSourceCharacter) -> DataSourceSnapshot:
    return DataSourceSnapshot(characters=list(characters))


# =============================================================
# 宏对齐
# =============================================================


def test_macros_are_aligned_to_host_names() -> None:
    """酒馆的 `{{char}}` / `{{user}}` 换成本项目的 `{{char_name}}` / `{{user_name}}`。

    不对齐的话它们会以字面量进 prompt，还会被宏解析器当成「未知状态变量」逐条告警。
    """
    assert align_macros("{{char}} 看着 {{user}}") == "{{char_name}} 看着 {{user_name}}"


def test_macro_alignment_is_case_insensitive_and_tolerates_spaces() -> None:
    assert align_macros("{{ CHAR }} 和 {{User}}") == "{{char_name}} 和 {{user_name}}"


def test_existing_host_macros_are_untouched() -> None:
    """已经是本项目的宏就不该被二次改写（`{{char_name}}` 不能被拆成 `{{char_name_name}}`）。"""
    assert align_macros("{{char_name}}") == "{{char_name}}"
    assert align_macros("{{user_name}}") == "{{user_name}}"
    assert align_macros("{{current_mood}}") == "{{current_mood}}"


def test_macros_are_aligned_in_mapped_persona() -> None:
    (persona,), _ = personas_from_snapshot(_snapshot(_card()))
    assert "{{char_name}}" in persona.prompt
    assert "{{user_name}}" in persona.prompt
    assert "{{char}}" not in persona.prompt


# =============================================================
# 角色名必须进提示词
# =============================================================


def test_identity_line_is_added_when_the_body_never_names_the_character() -> None:
    """★ 正文通篇不提自己名字时补一句「你是{{char_name}}。」。

    实测：本机 4 张酒馆卡 description 全空、正文取自开场白，里面一个 `{{char}}` 都没有。
    不补这一句，模型不知道自己在扮演谁——用户问「你叫什么」只能靠编。
    """
    card = _card(
        description="",
        personality="",
        scenario="",
        system_prompt="",
        first_message="你好呀，今天天气不错。",
    )

    (persona,), _ = personas_from_snapshot(_snapshot(card))

    assert persona.prompt.startswith("你是{{char_name}}。")
    assert persona.prompt.endswith("你好呀，今天天气不错。")


def test_identity_line_is_not_added_when_the_body_already_names_the_character() -> None:
    """正文里已经有 `{{char}}`（已对齐成 `{{char_name}}`）就不再多补一句。"""
    (persona,), _ = personas_from_snapshot(
        _snapshot(_card(description="{{char}} 是茶馆老板。", personality=""))
    )

    assert persona.prompt == "{{char_name}} 是茶馆老板。"


def test_identity_line_is_not_added_when_the_literal_name_appears() -> None:
    (persona,), _ = personas_from_snapshot(
        _snapshot(_card(name="苏澄", description="苏澄是茶馆老板。", personality=""))
    )

    assert persona.prompt == "苏澄是茶馆老板。"


def test_identity_line_covers_the_scenario_only_card() -> None:
    """只有 scenario（背景）的卡也要有人设正文——至少给得出身份。"""
    (persona,), _ = personas_from_snapshot(
        _snapshot(_card(description="", personality="", system_prompt=""))
    )

    assert persona.prompt == "你是{{char_name}}。"
    assert persona.background == "老城区的茶馆。"
    # 标题用**加身份句之前**的正文 → 不能变成一排「你是{{char_name}}。」
    assert persona.title == "来自酒馆的角色卡"


# =============================================================
# 字段映射
# =============================================================


def test_prompt_and_background_are_split_by_meaning() -> None:
    """人设正文管「是谁 / 怎么说话」，背景管「过往与世界观」——与 PersonaPreset 的分块对齐。"""
    card = _card(
        system_prompt="系统指令。",
        description="外观描述。",
        personality="性格描述。",
        scenario="世界观。",
        post_history_instructions="后置指令。",
    )

    (persona,), warnings = personas_from_snapshot(_snapshot(card))

    assert warnings == []
    assert persona.prompt == (
        "你是{{char_name}}。\n\n系统指令。\n\n外观描述。\n\n性格描述。"
    )
    assert persona.background == "世界观。\n\n后置指令。"


def test_empty_blocks_do_not_leave_blank_lines() -> None:
    (persona,), _ = personas_from_snapshot(_snapshot(_card(system_prompt="", personality="")))

    assert (
        persona.prompt
        == "{{char_name}} 是街角茶馆的老板，{{user_name}} 常来找 {{char_name}} 喝茶。"
    )


def test_tags_are_cleaned_and_deduplicated() -> None:
    card = _card(extra={"tags": ["温柔", "温柔", "", "  ", "日常"]})
    (persona,), _ = personas_from_snapshot(_snapshot(card))
    assert persona.tags == ["温柔", "日常"]


def test_too_many_tags_are_truncated_with_warning() -> None:
    card = _card(extra={"tags": [f"标签{i}" for i in range(25)]})
    (persona,), warnings = personas_from_snapshot(_snapshot(card))

    assert len(persona.tags) == 20
    assert any("标签超过 20 条" in warning for warning in warnings)


def test_creator_falls_back_to_tavern() -> None:
    """作者字段空着时标明来源，而不是冒充本项目原创（`AGENTS.md §6`）。"""
    (persona,), _ = personas_from_snapshot(_snapshot(_card()))
    assert persona.creator == "tavern"

    (named,), _ = personas_from_snapshot(_snapshot(_card(extra={"creator": "someone"})))
    assert named.creator == "someone"


def test_title_prefers_description_first_line() -> None:
    """酒馆卡没有「一句话定位」字段，就地取简介首行。"""
    (persona,), _ = personas_from_snapshot(_snapshot(_card(description="第一行。\n第二行。")))
    assert persona.title == "第一行。"


def test_title_falls_back_to_prompt_first_line() -> None:
    (persona,), _ = personas_from_snapshot(
        _snapshot(_card(description="", personality="只有性格。"))
    )
    assert persona.title == "只有性格。"


def test_title_has_a_placeholder_when_the_body_is_empty() -> None:
    """正文来源只剩背景时不能给个空标题（也不能把身份句当标题）。"""
    (persona,), _ = personas_from_snapshot(
        _snapshot(_card(description="", personality="", system_prompt=""))
    )

    assert persona.prompt == "你是{{char_name}}。"       # 身份兜底
    assert persona.background == "老城区的茶馆。"          # 背景还在（scenario）
    assert persona.title == "来自酒馆的角色卡"


# =============================================================
# 上限截断（persona 层是 mandatory，不受预算裁剪）
# =============================================================


def test_long_prompt_is_clipped_with_warning() -> None:
    """超长正文必须在这里截断：persona 层 `mandatory=True`，不受 PromptManager 裁剪。"""
    card = _card(description="很长的正文。" * 2000)  # 12000 字

    (persona,), warnings = personas_from_snapshot(_snapshot(card))

    assert len(persona.prompt) == 8000
    assert any("人设正文超过 8000 字，已截断" in warning for warning in warnings)


def test_long_background_is_clipped_with_warning() -> None:
    card = _card(scenario="很长的世界观。" * 2000)
    (persona,), warnings = personas_from_snapshot(_snapshot(card))

    assert len(persona.background) == 8000
    assert any("背景故事超过 8000 字" in warning for warning in warnings)


def test_long_name_and_description_are_clipped() -> None:
    card = _card(name="名" * 80, description="简" * 600)
    (persona,), warnings = personas_from_snapshot(_snapshot(card))

    assert len(persona.name) == 50
    assert len(persona.title) == 100
    assert len(persona.description) == 500
    assert any("角色名超过 50 字" in warning for warning in warnings)


# =============================================================
# 跳过与「读到了但用不上」
# =============================================================


def test_card_without_setting_fields_falls_back_to_the_greeting() -> None:
    """★ 设定字段全空时用开场白当人设正文。

    实测：本机 4 张酒馆卡（4/4）`description` / `personality` / `scenario` 全空，
    「是谁、怎么说话」全在开场白里。跳过它们 = 用户「读到了却一个角色都没有」。
    """
    card = _card(
        description="",
        personality="",
        scenario="",
        system_prompt="",
        first_message="你好呀。",
    )

    presets, warnings = personas_from_snapshot(_snapshot(card))

    (persona,) = presets
    assert persona.prompt == "你是{{char_name}}。\n\n你好呀。"
    assert persona.title == "你好呀。"   # 标题取自加身份句之前的正文首行
    assert any("人设正文取自开场白" in warning for warning in warnings)
    # 它正在当正文用，不该又报一句「开场白未使用」
    assert not any("未使用" in warning for warning in warnings)


def test_card_with_nothing_at_all_is_skipped() -> None:
    """设定与开场白都空 → 给不出任何可注入内容，不凭空造一个空壳角色。"""
    presets, warnings = personas_from_snapshot(
        _snapshot(
            _card(description="", personality="", scenario="", system_prompt="")
        )
    )

    assert presets == []
    assert any("设定与开场白全空" in warning for warning in warnings)


def test_unused_card_fields_are_reported_once() -> None:
    """开场白 / 示例对话本项目用不上，但必须让用户知道（否则会以为丢内容了）。"""
    cards = [
        _card(source_id="char#1", first_message="你好。"),
        _card(source_id="char#2", alternate_greetings=("备选一",)),
        _card(source_id="char#3", example_messages="<START>"),
    ]

    presets, warnings = personas_from_snapshot(_snapshot(*cards))

    assert len(presets) == 3
    unused = [w for w in warnings if "未使用" in w]
    assert len(unused) == 1  # 按类别汇总，不逐条刷屏
    for label in ("开场白", "备选开场白", "示例对话"):
        assert label in unused[0]


# =============================================================
# id：稳定、合法、与内嵌世界书 scope 同源
# =============================================================


def test_persona_id_is_stable_and_legal() -> None:
    card = _card()
    generated = persona_id_for(card)

    assert generated == persona_id_for(_card())  # 同来源 → 同 id
    assert generated == stable_item_id(PERSONA_ID_PREFIX, "demo-source", "char#1")
    assert generated.startswith("tbp-")
    assert generated.isascii() and "/" not in generated and "#" not in generated


def test_different_sources_get_different_ids() -> None:
    assert persona_id_for(_card()) != persona_id_for(_card(source="other"))
    assert persona_id_for(_card()) != persona_id_for(_card(source_id="char#2"))


# =============================================================
# 角色名 → persona id 的归属表（世界书 scope 与会话记忆**共用**）
# =============================================================


def test_scopes_by_character_name_matches_persona_id_for() -> None:
    """★ 表里的 id 必须与 `persona_id_for` 完全一致。

    世界书内嵌书的 `scope`、酒馆会话要写进哪个陪伴对象——两边共用这一个函数
    算 id，否则内容会挂在一个永远匹配不上的 id 上（表现为「导了却召不回」）。
    """
    card = _card(name="示例角色")

    scopes = scopes_by_character_name(_snapshot(card))

    assert scopes == {"示例角色": persona_id_for(card)}


def test_scopes_by_character_name_keeps_the_first_on_duplicates() -> None:
    """同名角色卡（罕见）取第一张：宁可少注入，也不能把两张卡的内容搞混。"""
    first = _card(name="同名", source_id="char#1")
    second = _card(name="同名", source_id="char#2")

    scopes = scopes_by_character_name(_snapshot(first, second))

    assert scopes == {"同名": persona_id_for(first)}


def test_scopes_by_character_name_empty_snapshot() -> None:
    assert scopes_by_character_name(DataSourceSnapshot()) == {}


# =============================================================
# 走插件注册表的聚合通路
# =============================================================


class _FakeDataSource:
    """最小数据源替身：只有 `read()`，产出中性快照。"""

    def __init__(self, snapshot: DataSourceSnapshot) -> None:
        self._snapshot = snapshot

    def read(self) -> DataSourceSnapshot:
        return self._snapshot


class _NotAPersonaSource:
    """别种 datasource：没有 `read()`，必须被跳过。"""

    def list_models(self) -> list[str]:
        return []


def _install_sources(tmp_path, *sources):
    """装上「只含给定数据源」的插件管理器，返回原管理器（用例负责恢复）。"""
    from app.plugins.manager import PluginManager, get_plugin_manager, set_plugin_manager
    from app.plugins.manifest import PluginManifest
    from app.plugins.registry import PluginRegistration, PluginRegistry

    registry = PluginRegistry()
    registry.register(
        PluginRegistration(
            manifest=PluginManifest(id="demo-source", display_name="示例数据源"),
            datasources=list(sources),
        )
    )
    original = get_plugin_manager()
    set_plugin_manager(PluginManager(registry, data_dir=tmp_path, plugin_dirs=[]))
    return original


def test_collect_walks_plugin_datasources(tmp_path) -> None:
    from app.plugins.manager import set_plugin_manager

    original = _install_sources(tmp_path, _FakeDataSource(_snapshot(_card())))
    try:
        presets, warnings = collect_datasource_personas()
    finally:
        set_plugin_manager(original)

    assert [preset.name for preset in presets] == ["示例角色"]
    assert warnings == []


def test_non_persona_datasources_are_ignored(tmp_path) -> None:
    from app.plugins.manager import set_plugin_manager

    original = _install_sources(
        tmp_path, _NotAPersonaSource(), _FakeDataSource(_snapshot(_card()))
    )
    try:
        presets, _ = collect_datasource_personas()
    finally:
        set_plugin_manager(original)

    assert [preset.name for preset in presets] == ["示例角色"]


def test_duplicate_across_sources_keeps_the_first(tmp_path) -> None:
    """同一张卡被两个数据源读到（id 相同）→ 留第一份，不出两个同名角色。"""
    from app.plugins.manager import set_plugin_manager

    snapshot = _snapshot(_card())
    original = _install_sources(
        tmp_path, _FakeDataSource(snapshot), _FakeDataSource(snapshot)
    )
    try:
        presets, _ = collect_datasource_personas()
    finally:
        set_plugin_manager(original)

    assert len(presets) == 1


# =============================================================
# 端到端：插件角色卡 → 对话链路的角色清单
# =============================================================


def test_tavern_personas_are_cached_and_invalidated(tmp_path) -> None:
    """`_tavern_personas()` 缓存外部角色卡，由 `invalidate_chat_graph()` 负责清。

    不缓存，界面每次打 `/chat/personas` 就重读一遍用户目录；
    不失效，插件启停 / 换数据目录后界面还拿着旧名单。
    """
    from app.api import chat as chat_module
    from app.plugins.manager import set_plugin_manager

    original = _install_sources(tmp_path, _FakeDataSource(_snapshot(_card())))
    try:
        chat_module.invalidate_chat_graph()
        first = chat_module._tavern_personas()
        assert [preset.name for preset in first] == ["示例角色"]
        assert chat_module._tavern_personas() is first  # 命中缓存

        chat_module.invalidate_chat_graph()
        assert chat_module._tavern_personas() is not first  # 重新取一份
    finally:
        set_plugin_manager(original)
        chat_module.invalidate_chat_graph()


def test_persona_catalog_includes_tavern_characters(tmp_path, monkeypatch) -> None:
    """★ 端到端：能读到的酒馆角色卡要出现在「陪伴对象」选择器里（`/chat/personas`）。

    这条链是：插件读图 → 宿主映射 → StudioStore 合并 → 界面选择器。
    刻意走 `get_studio_store()` 的**真实装配路径**（外部来源由 chat 注入），
    而不是测试自己塞一个 store——否则「到底注入了没有」没人守。
    """
    from fastapi.testclient import TestClient

    from app.api import chat as chat_module
    from app.main import app
    from app.plugins.manager import set_plugin_manager

    monkeypatch.setenv("STUDIO_DIR", str(tmp_path / "studio"))
    original = _install_sources(tmp_path, _FakeDataSource(_snapshot(_card())))
    try:
        chat_module.invalidate_chat_graph()
        chat_module.set_studio_store(None)  # 丢掉 conftest 注入的隔离 store
        catalog = TestClient(app).get("/chat/personas").json()
    finally:
        set_plugin_manager(original)
        chat_module.set_studio_store(None)
        chat_module.invalidate_chat_graph()

    summary = next(
        item for item in catalog["personas"] if item["id"] == persona_id_for(_card())
    )
    assert summary["name"] == "示例角色"
    assert summary["builtin"] is True  # 另一个人的卡：只读，要改只能「复制到我的」


def test_tavern_persona_is_selectable_as_companion(tmp_path, monkeypatch) -> None:
    """★ 端到端：它能真的当「陪伴对象」用——人设注入且 ``{{char_name}}`` 渲染成它的名字。

    只出现在清单里不算数：`/chat` 必须能把选定的人设渲染进 System Prompt。
    """
    from fastapi.testclient import TestClient

    from app.api import chat as chat_module
    from app.main import app
    from app.plugins.manager import set_plugin_manager

    monkeypatch.setenv("STUDIO_DIR", str(tmp_path / "studio"))
    original = _install_sources(tmp_path, _FakeDataSource(_snapshot(_card(name="苏澄"))))
    try:
        chat_module.invalidate_chat_graph()
        chat_module.set_studio_store(None)
        body = (
            TestClient(app)
            .post(
                "/chat",
                json={
                    "text": "你好",
                    "persona_id": persona_id_for(_card(name="苏澄")),
                    "user_name": "小林",
                },
            )
            .json()
        )
    finally:
        set_plugin_manager(original)
        chat_module.set_studio_store(None)
        chat_module.invalidate_chat_graph()

    prompt = body["system_prompt"]
    assert "苏澄" in prompt  # `{{char_name}}` 渲染成角色卡里的名字
    assert "{{char" not in prompt
    assert "小林" in prompt
