"""ST 预设存储与覆盖层测试（夹具全部自创，见 conftest.py 的合规说明）。"""

import json

import pytest

from app.prompts.st_compat import (
    DEFAULT_MEMORY_INJECTION,
    StPresetStore,
    parse_st_preset,
)


@pytest.fixture
def store(tmp_path) -> StPresetStore:
    """指向临时目录的存储实例（绝不碰 backend/data）。"""
    return StPresetStore(tmp_path / "presets")


# --------------------------------------------------------------------------
# 导入
# --------------------------------------------------------------------------


def test_import_and_list(store, make_preset) -> None:
    """导入后清单应反映条目数与启用数，且不返回正文。"""
    summary = store.import_preset(make_preset(), source_file="my-tavern-preset.json")

    assert summary.id == "my-tavern-preset"
    assert summary.name == "my-tavern-preset"
    assert summary.prompt_count == 10
    assert summary.enabled_count == 9             # 夹具里 reply-length 默认关闭
    assert summary.has_override is False

    listed = store.list_presets()
    assert [item.id for item in listed] == ["my-tavern-preset"]
    assert "content" not in json.dumps(listed[0].as_dict(), ensure_ascii=False)


def test_import_does_not_persist_sensitive_keys(store, make_preset) -> None:
    """落盘文件里不得出现端点/密钥类字段（安全红线）。"""
    store.import_preset(make_preset(), preset_id="safe")

    text = (store.root / "safe.json").read_text(encoding="utf-8")

    for secret in ("sk-secret-value", "sk-test-header", "sk-vendor-secret"):
        assert secret not in text
    for key in ("proxy_password", "custom_include_headers", "custom_url", "chat_completion_source"):
        assert key not in text
    assert "temperature" in text               # 采样参数必须保留


def test_import_accepts_json_text_and_bytes(store, make_preset) -> None:
    """dict / JSON 文本 / 带 BOM 的字节都应可导入。"""
    payload = json.dumps(make_preset(), ensure_ascii=False)

    store.import_preset(payload, preset_id="from-text")
    store.import_preset(payload.encode("utf-8-sig"), preset_id="from-bytes")
    store.import_preset(make_preset(), preset_id="from-dict")

    assert {item.id for item in store.list_presets()} == {"from-text", "from-bytes", "from-dict"}


def test_import_conflict_requires_overwrite(store, make_preset) -> None:
    """同 id 重复导入必须显式 overwrite，避免误覆盖用户的编辑。"""
    store.import_preset(make_preset(), preset_id="dup")

    with pytest.raises(ValueError):
        store.import_preset(make_preset(), preset_id="dup")

    store.patch_override("dup", {"sampling": {"temperature": 0.1}})
    store.import_preset(make_preset(), preset_id="dup", overwrite=True)
    # 覆盖导入会作废旧编辑（旧编辑对应的是旧文件内容）
    assert store.load("dup").preset.temperature == 0.9


def test_import_auto_allocates_id(store, make_preset) -> None:
    """同名来源文件二次导入应自动分配新 id，而不是覆盖。"""
    first = store.import_preset(make_preset(), source_file="tone.json")
    second = store.import_preset(make_preset(), source_file="tone.json")

    assert first.id == "tone"
    assert second.id == "tone-2"


def test_invalid_json_leaves_no_file(store) -> None:
    """解析失败必须不落盘（半成品文件比报错更糟）。"""
    with pytest.raises(ValueError):
        store.import_preset("{ not json", preset_id="broken")

    assert not (store.root / "broken.json").exists()


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "", "UPPER", "a..b", "with space"])
def test_illegal_preset_id_rejected(store, make_preset, bad_id) -> None:
    """非法 id（含目录穿越）必须拒绝。"""
    with pytest.raises(ValueError):
        store.import_preset(make_preset(), preset_id=bad_id)


# --------------------------------------------------------------------------
# 覆盖层
# --------------------------------------------------------------------------


def test_patch_override_applies_sampling_and_enabled(store, make_preset) -> None:
    """覆盖层应生效：采样参数与条目启用状态。"""
    store.import_preset(make_preset(), preset_id="ovr")
    store.patch_override(
        "ovr",
        {
            "sampling": {"temperature": 0.5},
            "prompts": {"reply-length": {"enabled": True}},
        },
    )

    parsed = store.load("ovr")

    assert parsed.preset.temperature == 0.5
    assert next(e.enabled for e in parsed.order if e.identifier == "reply-length") is True
    assert store.list_presets()[0].has_override is True


def test_patch_override_is_deep_merged(store, make_preset) -> None:
    """多次 patch 应深合并，而不是互相覆盖整块。"""
    store.import_preset(make_preset(), preset_id="merge")
    store.patch_override("merge", {"sampling": {"temperature": 0.5}})
    store.patch_override("merge", {"sampling": {"top_p": 0.7}})

    parsed = store.load("merge")

    assert parsed.preset.temperature == 0.5
    assert parsed.preset.top_p == 0.7


def test_override_content_edit_ignored_for_markers(store, make_preset) -> None:
    """占位条目的正文不可被覆盖（内容由运行时填充）。"""
    store.import_preset(make_preset(), preset_id="marker")
    store.patch_override("marker", {"prompts": {"chatHistory": {"content": "（测试语料）不该生效"}}})

    parsed = store.load("marker")

    assert parsed.get("chatHistory").content == ""
    assert any("占位条目" in w for w in parsed.warnings)


def test_override_role_and_order_validation(store, make_preset) -> None:
    """非法 role 覆盖被忽略；顺序覆盖只接受已知条目。"""
    store.import_preset(make_preset(), preset_id="valid")
    store.patch_override(
        "valid",
        {
            "prompts": {"main": {"role": "wizard"}},
            "prompt_order": ["mood-note", "ghost", "main"],
        },
    )

    parsed = store.load("valid")

    assert parsed.get("main").role == "system"                 # 非法 role 被忽略
    assert parsed.order[0].identifier == "mood-note"           # 顺序覆盖生效
    assert "ghost" not in [e.identifier for e in parsed.order]  # 未知条目被忽略
    assert any("wizard" in w for w in parsed.warnings)
    assert any("ghost" in w for w in parsed.warnings)


def test_reset_override_restores_import_state(store, make_preset) -> None:
    """重置后回到导入时的状态。"""
    store.import_preset(make_preset(), preset_id="reset")
    store.patch_override("reset", {"sampling": {"temperature": 0.1}})

    store.reset_override("reset")

    assert store.load("reset").preset.temperature == 0.9
    assert store.list_presets()[0].has_override is False


def test_raw_file_change_invalidates_override(store, make_preset) -> None:
    """用户在磁盘上替换了同名文件 → 旧编辑作废并记警告。"""
    store.import_preset(make_preset(), preset_id="hash")
    store.patch_override("hash", {"sampling": {"temperature": 0.1}})

    replaced = json.dumps(make_preset(temperature=0.7), ensure_ascii=False)
    (store.root / "hash.json").write_text(replaced, encoding="utf-8")

    parsed = store.load("hash")

    assert parsed.preset.temperature == 0.7
    assert not (store.root / "hash.override.json").exists()
    assert any("作废" in w for w in parsed.warnings)


# --------------------------------------------------------------------------
# 记忆注入配置
# --------------------------------------------------------------------------


def test_memory_injection_defaults_and_patch(store, make_preset) -> None:
    """记忆注入默认走扩展注入语义（IN_CHAT / depth=1 / system / order=100）。"""
    store.import_preset(make_preset(), preset_id="mi")

    assert store.get_memory_injection("mi") == DEFAULT_MEMORY_INJECTION

    store.patch_override("mi", {"memory_injection": {"depth": 3, "position": "in_prompt"}})
    config = store.get_memory_injection("mi")

    assert config["depth"] == 3
    assert config["position"] == "in_prompt"
    assert config["role"] == "system"                 # 未覆盖项保持默认
    assert config["order"] == 100


def test_memory_injection_illegal_values_fall_back(store, make_preset) -> None:
    """非法注入配置回退默认值，不抛错。"""
    store.import_preset(make_preset(), preset_id="mi2")
    store.patch_override(
        "mi2",
        {"memory_injection": {"position": "nowhere", "depth": -3, "role": "wizard"}},
    )

    config = store.get_memory_injection("mi2")

    assert config["position"] == "in_chat"
    assert config["depth"] == 1
    assert config["role"] == "system"


# --------------------------------------------------------------------------
# 导出与删除
# --------------------------------------------------------------------------


def test_export_applies_override_and_reparses(store, make_preset) -> None:
    """导出结果应是 ST 兼容 JSON，且能被重新解析（roundtrip）。"""
    store.import_preset(make_preset(), preset_id="exp")
    store.patch_override(
        "exp",
        {"sampling": {"temperature": 0.42}, "prompts": {"reply-length": {"enabled": True}}},
    )

    exported = store.export_preset("exp")
    reparsed = parse_st_preset(exported)

    assert exported["temperature"] == 0.42
    assert reparsed.preset.temperature == 0.42
    assert reparsed.preset.openai_max_tokens == 512
    # 采样参数写回 ST 字段名（max_tokens → openai_max_tokens）
    assert exported["openai_max_tokens"] == 512
    assert next(e.enabled for e in reparsed.order if e.identifier == "reply-length") is True


def test_export_raw_ignores_override(store, make_preset) -> None:
    """apply_override=False 时导出原始内容。"""
    store.import_preset(make_preset(), preset_id="raw")
    store.patch_override("raw", {"sampling": {"temperature": 0.42}})

    exported = store.export_preset("raw", apply_override=False)

    assert exported["temperature"] == 0.9


def test_delete_preset_removes_files_and_index(store, make_preset) -> None:
    """删除后文件、覆盖层与索引条目一并清理。"""
    store.import_preset(make_preset(), preset_id="del")
    store.patch_override("del", {"sampling": {"temperature": 0.1}})

    store.delete_preset("del")

    assert not store.has_preset("del")
    assert not (store.root / "del.override.json").exists()
    assert store.list_presets() == []
    with pytest.raises(FileNotFoundError):
        store.load("del")


def test_list_skips_corrupt_file(store, make_preset) -> None:
    """单个文件损坏不应让整份清单接口失败。"""
    store.import_preset(make_preset(), preset_id="willbreak")
    (store.root / "willbreak.json").write_text("{ not json", encoding="utf-8")

    assert store.list_presets() == []
    assert store.has_preset("willbreak") is True


def test_missing_preset_raises(store) -> None:
    """载入不存在的预设应抛 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        store.load("nope")


# --------------------------------------------------------------------------
# 思考开关（assembly.show_thoughts）
#
# 背景：`show_thoughts` 不是 `STPreset` 的声明字段（保存在 model_extra 里），
# 故 `apply_override` 的 sampling 分支（按 model_fields 过滤）触及不到它。
# AI 适配要靠这个开关关掉推理模式来提速，因此单独铺一条路径。
# --------------------------------------------------------------------------


def test_override_show_thoughts_switches_thinking(store, make_preset) -> None:
    """assembly.show_thoughts 覆盖应落到 enable_thinking 上。"""
    store.import_preset(make_preset(show_thoughts=True), preset_id="think")
    assert store.load("think").preset.enable_thinking is True

    store.patch_override("think", {"assembly": {"show_thoughts": False}})

    parsed = store.load("think")
    assert parsed.preset.enable_thinking is False
    assert store.get_override("think")["assembly"]["show_thoughts"] is False


def test_override_show_thoughts_is_written_back_on_export(store, make_preset) -> None:
    """导出时要把覆盖后的思考开关写回 show_thoughts（否则导出结果仍是预设原值）。"""
    store.import_preset(make_preset(show_thoughts=True), preset_id="think-export")
    store.patch_override("think-export", {"assembly": {"show_thoughts": False}})

    assert store.export_preset("think-export")["show_thoughts"] is False


def test_override_show_thoughts_absent_leaves_preset_value(store, make_preset) -> None:
    """未提供 show_thoughts 的预设不受影响（不凭空注入字段）。"""
    store.import_preset(make_preset(), preset_id="think-none")
    store.patch_override("think-none", {"assembly": {"use_sysprompt": True}})

    parsed = store.load("think-none")

    assert parsed.preset.enable_thinking is None
    assert "show_thoughts" not in store.export_preset("think-none")


def test_reset_override_restores_show_thoughts(store, make_preset) -> None:
    """清空覆盖层后思考开关回到预设原值。"""
    store.import_preset(make_preset(show_thoughts=True), preset_id="think-reset")
    store.patch_override("think-reset", {"assembly": {"show_thoughts": False}})

    store.reset_override("think-reset")

    assert store.load("think-reset").preset.enable_thinking is True


# --------------------------------------------------------------------------
# 采样参数键名归一（界面 / 详情用 max_tokens，模型字段是 openai_max_tokens）
# --------------------------------------------------------------------------


def test_override_max_tokens_alias_reaches_preset_field(store, make_preset) -> None:
    """界面用 `max_tokens` 提交长度上限，必须落到 `openai_max_tokens` 上。

    回归用例：两者不是同一个字段名。旧实现按 `model_fields` 过滤时把
    `max_tokens` 直接丢掉——补丁写进了覆盖层，但界面改了等于没改，
    重新载入时值会弹回原值。
    """
    store.import_preset(make_preset(openai_max_tokens=8192), preset_id="alias")

    store.patch_override("alias", {"sampling": {"max_tokens": 333}})

    parsed = store.load("alias")
    assert parsed.preset.sampling["max_tokens"] == 333
    # 导出写回 ST 字段名，保证往返一致
    assert store.export_preset("alias")["openai_max_tokens"] == 333


def test_override_unknown_sampling_key_is_ignored(store, make_preset) -> None:
    """不认识采样键名仍然忽略（归一不能变成「什么键都往里写」）。"""
    store.import_preset(make_preset(openai_max_tokens=8192), preset_id="alias-bad")

    store.patch_override("alias-bad", {"sampling": {"not_a_field": 1}})

    parsed = store.load("alias-bad")
    assert parsed.preset.sampling["max_tokens"] == 8192
    assert not hasattr(parsed.preset, "not_a_field")
