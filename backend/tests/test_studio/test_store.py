"""创作工坊存储门面（StudioStore）的单元测试。

覆盖四件事：
1. 内置 + 用户内容的合并视图（含「内置只读」语义）；
2. 用户内容的 CRUD 与落盘 round-trip（中文、多行正文、列表去重）；
3. 写接口的校验（必填项、触发条件、正则、归属角色）；
4. 「用户手改文件写坏了」时的容错（跳过 + 警告，不拖垮整份清单）。

内置资源一律用**假对象注入**：测试不读真实包目录，也不碰 backend/data。
"""

import pytest

from app.prompts.persona.loader import PersonaPreset
from app.studio.store import BuiltinReadOnlyError, StudioError, StudioStore
from app.worldbook.models import SCOPE_ALL, WorldBookEntry


@pytest.fixture
def builtin_persona() -> PersonaPreset:
    """假内置角色。"""
    return PersonaPreset(
        id="builtin-sister",
        name="内置角色",
        title="测试用内置角色",
        description="内置角色的简介",
        prompt="你是{{user_name}}的朋友。",
    )


@pytest.fixture
def builtin_entry() -> WorldBookEntry:
    """假内置世界书条目。"""
    return WorldBookEntry(
        id="builtin-night",
        title="内置条目·深夜模式",
        content="现在是深夜。",
        keys=["深夜"],
        priority=5,
    )


@pytest.fixture
def store(tmp_path, builtin_persona, builtin_entry) -> StudioStore:
    """指向临时目录 + 假内置的门面。"""
    return StudioStore(
        tmp_path / "studio",
        builtin_personas={builtin_persona.id: builtin_persona},
        builtin_entries=[builtin_entry],
    )


def _reload(tmp_path, *entries) -> StudioStore:
    """用同一目录重建门面（验证「写进去的能读出来」）。"""
    return StudioStore(
        tmp_path / "studio", builtin_personas={}, builtin_entries=list(entries)
    )


# ---------- 角色：清单与合并视图 ----------


def test_list_puts_builtin_first_with_flag(store):
    created = store.create_persona(name="我的角色", title="My Character", prompt="你好")
    summaries, warnings = store.list_personas()

    assert warnings == []
    assert [item.id for item in summaries] == ["builtin-sister", created.id]
    assert [item.builtin for item in summaries] == [True, False]


def test_summary_does_not_carry_persona_body(store):
    store.create_persona(name="我的角色", title="Mine", prompt="很长的正文")
    summaries, _ = store.list_personas()
    mine = next(item for item in summaries if not item.builtin)
    assert "prompt" not in mine.as_dict()


def test_all_personas_returns_id_index(store):
    created = store.create_persona(name="我的角色", title="Mine", prompt="你好")
    presets, warnings = store.all_personas()
    assert warnings == []
    assert set(presets) == {"builtin-sister", created.id}


# ---------- 角色：创建 / 更新 / 删除 / 复制 ----------


def test_create_persona_persists_and_round_trips(tmp_path, store):
    created = store.create_persona(
        name="我的角色",
        title="My Character",
        prompt="你是我的伙伴。",
        background="我们相识于一个雨天。",
        tags=["朋友", "朋友", "  "],
    )

    assert created.id == "user-my-character"  # 由标题 slug 自动生成
    assert created.tags == ["朋友"]  # 去重 + 去空白
    assert created.creator == "user"

    preset, builtin = _reload(tmp_path).get_persona(created.id)
    assert builtin is False
    assert (preset.name, preset.background, preset.prompt) == (
        "我的角色",
        "我们相识于一个雨天。",
        "你是我的伙伴。",
    )


def test_multiline_prompt_written_as_block_scalar(tmp_path, store):
    prompt = "第一行\n第二行\n\n第四行"
    created = store.create_persona(name="多行", title="Multi", prompt=prompt)

    raw = (tmp_path / "studio" / "personas" / f"{created.id}.yaml").read_text(
        encoding="utf-8"
    )
    # 块标量（|-）：用户手改 YAML 时正文保持可读，而不是被转义成一整行
    assert "|-" in raw
    assert created.prompt == prompt


@pytest.mark.parametrize(
    "kwargs",
    [{"name": "   ", "prompt": "正文"}, {"name": "名字", "prompt": ""}],
)
def test_create_persona_requires_name_and_prompt(store, kwargs):
    with pytest.raises(StudioError):
        store.create_persona(**kwargs)


def test_update_persona_only_touches_given_fields(store):
    created = store.create_persona(name="旧名", title="Old", prompt="旧正文")

    updated = store.update_persona(
        created.id, name="新名", background="新背景", description=""
    )

    assert (updated.name, updated.background, updated.description) == ("新名", "新背景", "")
    again, _ = store.get_persona(created.id)
    assert again.name == "新名"
    assert again.prompt == "旧正文"  # 未传的字段保持原值


def test_update_persona_keeps_id_even_if_name_changes(store):
    created = store.create_persona(name="旧名", title="Old", prompt="正文")
    updated = store.update_persona(created.id, name="完全不同的名字")
    # id 是记忆隔离命名空间，改名字不该换 id（否则等于换一整套记忆）
    assert updated.id == created.id


def test_delete_user_persona_removes_file(tmp_path, store):
    created = store.create_persona(name="我的角色", title="Mine", prompt="正文")
    store.delete_persona(created.id)

    assert not (tmp_path / "studio" / "personas" / f"{created.id}.yaml").exists()
    with pytest.raises(FileNotFoundError):
        store.get_persona(created.id)


def test_builtin_persona_is_read_only(store):
    with pytest.raises(BuiltinReadOnlyError):
        store.update_persona("builtin-sister", name="改名")
    with pytest.raises(BuiltinReadOnlyError):
        store.delete_persona("builtin-sister")


def test_duplicate_builtin_persona_creates_editable_copy(store):
    copy = store.duplicate_persona("builtin-sister")

    assert copy.id != "builtin-sister"
    assert copy.id.startswith("user-")
    assert copy.prompt == "你是{{user_name}}的朋友。"
    assert copy.name.endswith("（副本）")

    # 副本可编辑，原内置角色不受影响
    store.update_persona(copy.id, name="我的版本")
    original, builtin = store.get_persona("builtin-sister")
    assert builtin is True
    assert original.name == "内置角色"


# ---------- 角色：坏文件容错与 id 安全 ----------


def test_broken_user_file_is_skipped_with_warning(store, tmp_path):
    bad = tmp_path / "studio" / "personas" / "user-broken.yaml"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("name: [未闭合\n", encoding="utf-8")

    summaries, warnings = store.list_personas()

    assert [item.id for item in summaries] == ["builtin-sister"]
    assert any("user-broken.yaml" in warning for warning in warnings)


def test_file_id_mismatch_is_skipped_with_warning(store, tmp_path):
    path = tmp_path / "studio" / "personas" / "user-mismatch.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "id: user-other\nname: 名字\ntitle: 定位\ndescription: 简介\nprompt: 正文\n",
        encoding="utf-8",
    )

    summaries, warnings = store.list_personas()

    assert [item.id for item in summaries] == ["builtin-sister"]
    assert any("不一致" in warning for warning in warnings)


def test_chinese_title_falls_back_to_random_id(store):
    persona = store.create_persona(name="苏澄", prompt="正文")
    assert persona.id.startswith("user-")
    assert len(persona.id) == len("user-") + 6  # 中文标题无 ascii slug → 随机后缀


def test_illegal_and_missing_ids(store):
    with pytest.raises(FileNotFoundError):
        store.get_persona("user-nobody")
    with pytest.raises(StudioError):
        store.get_persona("../evil")  # 目录穿越必须被拒


# ---------- 世界书条目：创建 / 更新 / 删除 ----------


def test_create_entry_round_trips(tmp_path, store):
    entry = store.create_entry(
        title="我的设定",
        content="正文\n第二行",
        keys=["樱花", "樱花", " "],
        regex=[r"紫\w+"],
        vector_text="心口发闷",
        vector_threshold=0.66,
        priority=3,
    )

    assert entry.id.startswith("user-")
    assert entry.keys == ["樱花"]
    assert entry.scope == SCOPE_ALL  # 缺省对所有角色生效

    again, builtin = _reload(tmp_path).get_entry(entry.id)
    assert builtin is False
    assert again.regex == [r"紫\w+"]
    assert (again.vector_text, again.vector_threshold, again.priority) == ("心口发闷", 0.66, 3)


def test_create_entry_requires_trigger(store):
    with pytest.raises(StudioError, match="触发条件"):
        store.create_entry(title="没有触发词", content="正文")


def test_create_entry_rejects_invalid_regex(store):
    # 非法正则必须写入前拦住：matcher 直接 re.search，坏正则会炸掉每一轮对话
    with pytest.raises(StudioError, match="正则非法"):
        store.create_entry(title="坏正则", content="正文", regex=["(未闭合"])


def test_create_entry_requires_title_and_content(store):
    with pytest.raises(StudioError):
        store.create_entry(title="  ", content="正文", keys=["词"])
    with pytest.raises(StudioError):
        store.create_entry(title="标题", content=" ", keys=["词"])


def test_update_entry_and_delete_entry(store):
    entry = store.create_entry(title="设定", content="正文", keys=["词"])

    updated = store.update_entry(entry.id, content="新正文", priority=9, enabled=False)
    assert (updated.content, updated.priority, updated.enabled) == ("新正文", 9, False)

    store.delete_entry(entry.id)
    with pytest.raises(FileNotFoundError):
        store.get_entry(entry.id)


def test_update_entry_rejects_removing_all_triggers(store):
    entry = store.create_entry(title="设定", content="正文", keys=["词"])
    with pytest.raises(StudioError, match="触发条件"):
        store.update_entry(entry.id, keys=[])


# ---------- 世界书条目：归属（scope）----------


def test_entry_scope_must_reference_known_persona(store):
    with pytest.raises(StudioError, match="归属角色不存在"):
        store.create_entry(title="绑定幽灵角色", content="正文", keys=["词"], scope="nobody")

    with pytest.raises(StudioError):
        store.create_entry(title="非法归属", content="正文", keys=["词"], scope="../evil")


def test_entry_bound_to_created_persona(store):
    persona = store.create_persona(name="我的角色", title="Mine", prompt="正文")

    entry = store.create_entry(
        title="专属设定", content="只有这个角色知道", keys=["秘密"], scope=persona.id
    )

    assert entry.scope == persona.id
    assert entry.applies_to(persona.id) is True
    assert entry.applies_to("builtin-sister") is False
    assert store.count_bound_entries(persona.id) == 1
    assert store.count_bound_entries("builtin-sister") == 0


def test_global_entry_applies_to_everyone(store):
    entry = store.create_entry(title="全局设定", content="对所有人生效", keys=["世界"])
    assert entry.scope == SCOPE_ALL
    assert entry.applies_to("builtin-sister") is True


def test_update_entry_can_change_scope(store):
    persona = store.create_persona(name="我的角色", title="Mine", prompt="正文")
    entry = store.create_entry(title="设定", content="正文", keys=["词"])

    updated = store.update_entry(entry.id, scope=persona.id)

    assert updated.scope == persona.id
    assert store.count_bound_entries(persona.id) == 1


# ---------- 内置条目：只读 + 可停用（停用偏好只写用户侧文件）----------


def test_builtin_entry_is_read_only_except_enable_switch(store, builtin_entry):
    with pytest.raises(BuiltinReadOnlyError):
        store.update_entry(builtin_entry.id, content="改内容")
    with pytest.raises(BuiltinReadOnlyError):
        store.delete_entry(builtin_entry.id)


def test_enable_switch_rejects_user_entry(store):
    entry = store.create_entry(title="我的条目", content="正文", keys=["词"])
    with pytest.raises(StudioError, match="不是内置条目"):
        store.set_builtin_entry_enabled(entry.id, False)


def test_disabling_builtin_entry_never_touches_builtin_object(tmp_path, store, builtin_entry):
    store.set_builtin_entry_enabled(builtin_entry.id, False)

    # ① 内置对象本身没被改
    assert builtin_entry.enabled is True

    # ② 合并视图里已停用，且停用偏好落在用户侧文件
    entries, _ = store.all_entries()
    assert [entry.id for entry in entries] == [builtin_entry.id]
    assert entries[0].enabled is False
    assert (tmp_path / "studio" / "worldbook" / "disabled_builtin.json").is_file()

    # ③ 新实例（同目录）仍记得停用
    reloaded = _reload(tmp_path, builtin_entry)
    entries_again, _ = reloaded.all_entries()
    assert entries_again[0].enabled is False

    # ④ 恢复启用后清单文件被清掉（不留空清单造成误解）
    reloaded.set_builtin_entry_enabled(builtin_entry.id, True)
    assert reloaded.disabled_builtin_ids() == set()
    assert not (tmp_path / "studio" / "worldbook" / "disabled_builtin.json").exists()
    assert reloaded.get_entry(builtin_entry.id)[0].enabled is True


def test_broken_disabled_file_falls_back_to_enabled(store, tmp_path, builtin_entry):
    path = tmp_path / "studio" / "worldbook" / "disabled_builtin.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 不是合法 JSON", encoding="utf-8")

    assert store.disabled_builtin_ids() == set()
    entries, _ = store.all_entries()
    assert entries[0].enabled is True


# ---------- 字段长度上限（防超长内容把必留的人设层撑爆）----------


def test_create_persona_rejects_oversized_prompt(store):
    with pytest.raises(StudioError, match="人设正文最多"):
        store.create_persona(name="超长", prompt="字" * 8001)


def test_create_persona_rejects_oversized_background(store):
    with pytest.raises(StudioError, match="背景故事最多"):
        store.create_persona(name="超长", prompt="正文", background="字" * 8001)


def test_update_persona_rejects_oversized_name(store):
    created = store.create_persona(name="角色", title="Role", prompt="正文")
    with pytest.raises(StudioError, match="角色名最多"):
        store.update_persona(created.id, name="名" * 51)
    # 被拒后原值不变
    assert store.get_persona(created.id)[0].name == "角色"


def test_create_entry_rejects_oversized_content(store):
    with pytest.raises(StudioError, match="条目正文最多"):
        store.create_entry(title="超长", content="字" * 4001, keys=["词"])


def test_create_entry_rejects_too_many_triggers(store):
    with pytest.raises(StudioError, match="关键词最多"):
        store.create_entry(
            title="触发词过多", content="正文", keys=[f"k{i}" for i in range(51)]
        )


def test_update_entry_rejects_oversized_keyword(store):
    entry = store.create_entry(title="设定", content="正文", keys=["词"])
    with pytest.raises(StudioError, match="关键词"):
        store.update_entry(entry.id, keys=["词" * 101])
