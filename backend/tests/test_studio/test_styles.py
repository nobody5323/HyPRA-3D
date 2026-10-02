"""文风预设（自定义文风）的存储层测试。

覆盖四件事：
1. 内置 + 用户文风的合并视图与「内置只读」语义（同角色 / 世界书那套）；
2. 用户文风的 CRUD 与落盘 round-trip（中文、多行指令、示例对话、采样参数）；
3. 写接口的校验：**采样参数白名单与取值区间**、示例对话必须成对、风格指令必填；
4. 「用户手改文件写坏了」时的容错（跳过 + 警告，不拖垮整份清单）。

内置资源一律用**假对象注入**：测试不读真实包目录，也不碰 backend/data。
"""

import pytest

from app.prompts.style.models import StyleExample, StylePreset
from app.studio.store import BuiltinReadOnlyError, StudioError, StudioStore


@pytest.fixture
def builtin_style() -> StylePreset:
    """假内置文风。"""
    return StylePreset(
        id="builtin-brief",
        name="内置简短",
        description="内置的简短文风",
        tags=["简短"],
        style_prompt="惜字。一次只说一两句。",
        avoid=["长篇分析"],
        examples=[StyleExample(user="我最近失眠", assistant="几天了？")],
        sampling={"temperature": 0.8},
        conflicts_with=["健谈"],
    )


@pytest.fixture
def store(tmp_path, builtin_style) -> StudioStore:
    """指向临时目录 + 假内置的门面（角色与条目留空，只测文风）。"""
    return StudioStore(
        tmp_path / "studio",
        builtin_personas={},
        builtin_entries=[],
        builtin_styles={builtin_style.id: builtin_style},
    )


# ---------- 合并视图 ----------


def test_lists_builtin_and_marks_it_readonly(store) -> None:
    summaries, warnings = store.list_styles()
    assert warnings == []
    assert [item.id for item in summaries] == ["builtin-brief"]
    assert summaries[0].builtin is True
    # 清单刻意不带风格指令与示例正文（详情接口才给）
    assert summaries[0].examples == 1


def test_create_writes_round_trip(store) -> None:
    created = store.create_style(
        name="深夜电台",
        style_prompt="像深夜电台主持人一样低声说话。\n语速慢，句子短。",
        description="低音量、慢节奏",
        tags=["温柔", "慢", "温柔"],  # 重复项应被去掉
        avoid=["感叹号"],
        examples=[{"user": "睡不着", "assistant": "那就别急着睡。我在。"}],
        sampling={"temperature": 0.75, "max_tokens": 200},
        conflicts_with=["聒噪"],
    )

    assert created.id.startswith("user-")
    # 落盘后重读：中文、多行、列表、示例、采样都要原样回来
    path = store.style_dir / f"{created.id}.yaml"
    assert path.is_file()
    again, builtin = store.get_style(created.id)
    assert builtin is False
    assert again.name == "深夜电台"
    assert again.style_prompt == "像深夜电台主持人一样低声说话。\n语速慢，句子短。"
    assert again.tags == ["温柔", "慢"]
    assert again.example_count == 1
    assert again.sampling["temperature"] == 0.75


def test_style_id_never_collides_with_other_user_content(store) -> None:
    """id 分配必须看全量（角色 / 条目 / 文风），否则同名文件会互相覆盖。"""
    persona = store.create_persona(name="radio", prompt="你是电台主持人。")
    assert persona.id == "user-radio"

    # 同一 slug 再建文风：必须让位（否则 user-radio.yaml 会与角色文件同名）
    style = store.create_style(name="radio", style_prompt="低声说话。")
    assert style.id != persona.id
    assert style.id.startswith("user-")

    entry = store.create_entry(title="radio", content="收音机在响。", keys=["收音机"])
    assert entry.id not in {persona.id, style.id}


# ---------- 校验 ----------


def test_style_prompt_is_required(store) -> None:
    with pytest.raises(StudioError, match="风格指令不能为空"):
        store.create_style(name="空的", style_prompt="   ")


def test_unknown_sampling_key_is_rejected(store) -> None:
    """拼错的键不会被任何下游拦住（只会静默无效），必须在写入前失败。"""
    with pytest.raises(StudioError, match="不支持的采样参数"):
        store.create_style(name="拼错", style_prompt="低声。", sampling={"temperatur": 0.8})


@pytest.mark.parametrize(
    ("key", "value"),
    [("temperature", 80.0), ("top_p", 3.0), ("max_tokens", 0.0), ("frequency_penalty", -9.0)],
)
def test_out_of_range_sampling_is_rejected(store, key, value) -> None:
    """文风的采样优先级最高，一个量级错误会让每一轮对话都不可用。"""
    with pytest.raises(StudioError, match="应在"):
        store.create_style(name="越界", style_prompt="低声。", sampling={key: value})


def test_non_numeric_sampling_is_rejected(store) -> None:
    with pytest.raises(StudioError, match="必须是数字"):
        store.create_style(name="非数字", style_prompt="低声。", sampling={"top_p": "abc"})


def test_example_needs_both_sides(store) -> None:
    """只有 user 没有 assistant 的「示例」毫无示范价值，应当直接拒绝。"""
    with pytest.raises(StudioError, match="目标回应"):
        store.create_style(
            name="半截示例",
            style_prompt="低声。",
            examples=[{"user": "在吗", "assistant": "  "}],
        )


def test_builtin_style_is_read_only(store) -> None:
    with pytest.raises(BuiltinReadOnlyError):
        store.update_style("builtin-brief", name="改内置")


def test_duplicate_builtin_creates_editable_copy(store) -> None:
    copy = store.duplicate_style("builtin-brief")
    assert copy.id != "builtin-brief"
    assert copy.name == "内置简短（副本）"
    assert copy.style_prompt == "惜字。一次只说一两句。"
    assert copy.example_count == 1
    assert copy.sampling["temperature"] == 0.8
    # 副本可编辑
    updated = store.update_style(copy.id, name="我的简短")
    assert updated.name == "我的简短"


def test_delete_builtin_hides_it_without_touching_package(store) -> None:
    """删除内置文风只写用户侧隐藏清单，内置对象本身不动。"""
    store.delete_style("builtin-brief")
    assert [item.id for item in store.list_styles()[0]] == []
    assert store.deleted_builtin_style_ids() == {"builtin-brief"}
    # 内置资源仍在（只是被隐藏）——再次读取内置字典依然拿得到
    assert "builtin-brief" in store._builtin_styles()


def test_delete_user_style_removes_file(store) -> None:
    created = store.create_style(name="临时", style_prompt="低声。")
    path = store.style_dir / f"{created.id}.yaml"
    assert path.is_file()
    store.delete_style(created.id)
    assert not path.is_file()
    with pytest.raises(FileNotFoundError):
        store.get_style(created.id)


def test_delete_is_not_cascading(store) -> None:
    """删文风不该牵连角色 / 条目（文风不承载会话与记忆）。"""
    store.delete_style("builtin-brief")
    assert store.list_personas()[0] == []
    assert store.list_entries()[0] == []


def test_unknown_style_raises_not_found(store) -> None:
    with pytest.raises(FileNotFoundError):
        store.get_style("user-nope")


def test_illegal_id_is_rejected(store) -> None:
    with pytest.raises(StudioError):
        store.get_style("../../etc/passwd")


# ---------- 容错 ----------


def test_broken_user_file_is_skipped_with_warning(store) -> None:
    """用户手改坏一个文件，不该让整份文风清单都用不了。"""
    store.style_dir.mkdir(parents=True, exist_ok=True)
    (store.style_dir / "broken.yaml").write_text("id: broken\nname: [未闭合\n", encoding="utf-8")
    (store.style_dir / "mismatch.yaml").write_text(
        "id: other\nname: 对不上\ndescription: 对不上\nstyle_prompt: 低声。\n",
        encoding="utf-8",
    )

    summaries, warnings = store.list_styles()
    assert [item.id for item in summaries] == ["builtin-brief"]
    assert len(warnings) == 2
    assert any("解析失败" in item for item in warnings)
    assert any("与文件名不一致" in item for item in warnings)


def test_user_file_with_builtin_id_is_ignored(store) -> None:
    """用户文件与内置同名时内置优先（并记警告），否则内置文风会被悄悄顶掉。"""
    store.style_dir.mkdir(parents=True, exist_ok=True)
    (store.style_dir / "builtin-brief.yaml").write_text(
        "id: builtin-brief\nname: 冒名\ndescription: 冒名\nstyle_prompt: 冒名。\n",
        encoding="utf-8",
    )
    summaries, warnings = store.list_styles()
    assert [item.name for item in summaries] == ["内置简短"]
    assert any("同名" in item for item in warnings)
