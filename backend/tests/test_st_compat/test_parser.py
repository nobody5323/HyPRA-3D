"""ST 预设解析与归一化测试（夹具全部自创，见 conftest.py 的合规说明）。"""

import json

import pytest

from app.prompts.st_compat import (
    parse_st_preset,
    parse_st_preset_text,
    slugify,
)

# --------------------------------------------------------------------------
# 基本字段
# --------------------------------------------------------------------------


def test_parse_full_preset_resolves_sampling(make_preset) -> None:
    """采样参数与组装控制字段应完整解析（None 项不出现在 sampling 里）。"""
    parsed = parse_st_preset(make_preset())

    assert parsed.preset.temperature == 0.9
    assert parsed.preset.top_p == 0.95
    assert parsed.preset.frequency_penalty == 0.2
    assert parsed.preset.presence_penalty == 0.1
    assert parsed.preset.top_k == 40
    assert parsed.preset.min_p == 0.05
    assert parsed.preset.openai_max_tokens == 512
    assert parsed.preset.context_budget == 8192
    assert parsed.preset.names_behavior == 0
    assert parsed.preset.squash_system_messages is False
    assert parsed.source_format == "chat"

    # 未提供的采样项不应出现（合并时才会跳过，而不是覆盖成 None）
    assert "repetition_penalty" not in parsed.preset.sampling
    assert parsed.preset.sampling["max_tokens"] == 512


def test_assembly_switches_parsed(make_preset) -> None:
    """组装开关（names_behavior / squash / use_sysprompt）应原样解析。"""
    parsed = parse_st_preset(
        make_preset(names_behavior=1, squash_system_messages=True, use_sysprompt=True)
    )

    assert parsed.preset.names_behavior == 1
    assert parsed.preset.squash_system_messages is True
    assert parsed.preset.use_sysprompt is True


def test_marker_flags_and_in_chat(make_preset) -> None:
    """marker 与 In-Chat 判定应正确。"""
    parsed = parse_st_preset(make_preset())

    assert parsed.get("chatHistory").is_marker is True
    assert parsed.get("main").is_marker is False
    assert parsed.get("mood-note").is_in_chat is True
    assert parsed.get("mood-note").injection_depth == 2
    assert parsed.get("mood-note").injection_order == 50
    assert parsed.get("main").is_in_chat is False


# --------------------------------------------------------------------------
# 归一化：正文与标识
# --------------------------------------------------------------------------


def test_runtime_markers_cleared_but_text_prompts_kept(make_preset, texts) -> None:
    """运行时填充类 marker 的清空，不能误伤 main/jailbreak 这类自带正文的条目。"""
    data = make_preset()
    for item in data["prompts"]:
        if item["identifier"] == "chatHistory":
            item["content"] = "（测试语料）不该保留的正文"

    parsed = parse_st_preset(data)

    assert parsed.get("chatHistory").content == ""
    assert parsed.get("main").content == texts["main"]
    assert parsed.get("jailbreak").content == texts["jailbreak"]
    assert any("占位条目" in w for w in parsed.warnings)


def test_missing_identifier_generated_from_name(make_preset) -> None:
    """缺 identifier 的条目应按名称生成可用的标识。"""
    data = make_preset()
    data["prompts"].append({"name": "Reply Tone", "content": "（测试语料）"})

    parsed = parse_st_preset(data)

    assert "reply-tone" in parsed.prompts_by_id
    assert any("缺少 identifier" in w for w in parsed.warnings)


def test_duplicate_identifier_renamed(make_preset) -> None:
    """重复 identifier 应自动加后缀，保证索引不互相覆盖。"""
    data = make_preset()
    data["prompts"].append({"identifier": "reply-length", "name": "重复项", "content": "（测试语料）"})

    parsed = parse_st_preset(data)

    assert "reply-length-2" in parsed.prompts_by_id
    assert any("重复" in w for w in parsed.warnings)


def _item(data: dict, identifier: str) -> dict:
    """按标识取条目（别用下标：夹具增删条目时下标会静默错位）。"""
    return next(item for item in data["prompts"] if item["identifier"] == identifier)


def test_role_alias_model_maps_to_assistant(make_preset) -> None:
    """社区预设里 Gemini 风格的 `role: model` 应映射为 assistant，而不是回退 system。"""
    data = make_preset()
    _item(data, "reply-length")["role"] = "model"

    parsed = parse_st_preset(data)

    assert parsed.get("reply-length").role == "assistant"
    # 已知别名不该产生“不受支持”噪声（真实预设里一次会出现好几条）
    assert not any("不受支持" in w for w in parsed.warnings)


def test_role_alias_ai_maps_to_assistant(make_preset) -> None:
    """`ai` / `bot` 等旧写法同样归一为 assistant。"""
    data = make_preset()
    _item(data, "reply-length")["role"] = "AI"

    parsed = parse_st_preset(data)

    assert parsed.get("reply-length").role == "assistant"
    assert not any("不受支持" in w for w in parsed.warnings)


def test_unknown_role_still_warns(make_preset) -> None:
    """真正未知的角色仍然回退 system 并告警。"""
    data = make_preset()
    _item(data, "main")["role"] = "wizard"

    parsed = parse_st_preset(data)

    assert parsed.get("main").role == "system"
    assert any("wizard" in w for w in parsed.warnings)


def test_invalid_role_falls_back_to_system(make_preset) -> None:
    """非法角色应回退 system 并留下警告，而不是让整份预设导入失败。"""
    data = make_preset()
    data["prompts"][0]["role"] = "wizard"

    parsed = parse_st_preset(data)

    assert parsed.get("main").role == "system"
    assert any("wizard" in w for w in parsed.warnings)


def test_none_fields_do_not_break_parsing(make_preset) -> None:
    """ST 预设里常见显式 null，应视为"未提供"而不是报错。"""
    data = make_preset()
    data["prompts"][0]["content"] = None
    data["prompts"][0]["name"] = None
    data["prompts"][7]["injection_trigger"] = None

    parsed = parse_st_preset(data)

    assert parsed.get("main").content == ""
    assert parsed.get("main").display_name == "main"
    assert parsed.get("reply-length").injection_trigger == []


# --------------------------------------------------------------------------
# 顺序表
# --------------------------------------------------------------------------


def test_missing_prompt_order_falls_back_to_definition_order(make_preset) -> None:
    """缺顺序表时按条目定义顺序生成，且全部启用。"""
    parsed = parse_st_preset(make_preset(prompt_order=[]))

    assert [e.identifier for e in parsed.order] == [
        item.identifier for item in parsed.preset.prompts
    ]
    assert all(e.enabled for e in parsed.order)
    assert parsed.order_index == -1
    assert any("未提供条目顺序" in w for w in parsed.warnings)


def test_multiple_orders_prefers_the_one_with_more_entries(make_preset) -> None:
    """多份顺序表应取**覆盖条目最多**的那份（并列取靠前）。

    真实场景：社区预设导出常见「全局 11 条 + 目标角色 55 条」，
    机械取第 1 份会让其余 100+ 个条目全部显成「未启用」。
    """
    data = make_preset()
    full_order = list(data["prompt_order"][0]["order"])
    data["prompt_order"][0]["order"] = full_order[:2]          # 全局那份很小
    data["prompt_order"].append({"character_id": 100001, "order": full_order})

    parsed = parse_st_preset(data)

    assert parsed.order_index == 1
    assert len(parsed.order) == len(full_order)
    assert any("覆盖条目最多" in w for w in parsed.warnings)


def test_multiple_orders_keeps_first_when_it_is_richest(make_preset) -> None:
    """第 1 份条目更多时仍选第 1 份。"""
    data = make_preset()
    data["prompt_order"].append(
        {"character_id": 100001, "order": [{"identifier": "main", "enabled": False}]}
    )

    parsed = parse_st_preset(data)

    assert parsed.order_index == 0
    assert any("份顺序表" in w for w in parsed.warnings)
    assert parsed.get("main") is not None


def test_dangling_reference_removed_and_unreferenced_warned(make_preset) -> None:
    """顺序表悬空引用移除；定义了但没进顺序表的条目提示"未启用"。"""
    data = make_preset()
    data["prompt_order"][0]["order"].append({"identifier": "ghost", "enabled": True})
    data["prompt_order"][0]["order"] = [
        entry for entry in data["prompt_order"][0]["order"] if entry["identifier"] != "mood-note"
    ]

    parsed = parse_st_preset(data)
    identifiers = [entry.identifier for entry in parsed.order]

    assert "ghost" not in identifiers
    assert "mood-note" not in identifiers
    assert any("ghost" in w for w in parsed.warnings)
    assert any("未列入任何顺序表" in w for w in parsed.warnings)


def test_duplicate_order_entry_deduplicated(make_preset) -> None:
    """顺序表重复引用同一条目应去重。"""
    data = make_preset()
    data["prompt_order"][0]["order"].append({"identifier": "main", "enabled": False})

    parsed = parse_st_preset(data)
    identifiers = [entry.identifier for entry in parsed.order]

    assert identifiers.count("main") == 1
    assert any("去重" in w for w in parsed.warnings)


def test_enabled_items_respects_order_and_flags(make_preset) -> None:
    """已启用条目按顺序表顺序返回，关闭的条目不出现。"""
    parsed = parse_st_preset(make_preset())
    identifiers = [item.identifier for item in parsed.enabled_items]

    assert identifiers[0] == "main"
    assert "reply-length" not in identifiers   # 夹具里默认关闭
    assert "mood-note" in identifiers


# --------------------------------------------------------------------------
# 敏感字段与未知字段
# --------------------------------------------------------------------------


def test_sensitive_keys_stripped_and_never_kept(make_preset) -> None:
    """端点/密钥类字段应剥离，且值不出现在解析结果里。"""
    parsed = parse_st_preset(make_preset())

    assert "proxy_password" in parsed.stripped_keys
    assert "custom_include_headers" in parsed.stripped_keys
    assert "vendor_some_api_key" in parsed.stripped_keys   # 后缀兜底匹配

    serialized = json.dumps(parsed.raw, ensure_ascii=False)
    for secret in ("sk-secret-value", "sk-test-header", "sk-vendor-secret"):
        assert secret not in serialized
    assert "chat_completion_source" not in parsed.raw
    assert "custom_url" not in parsed.raw

    # 采样与非敏感字段必须原样保留
    assert parsed.raw["temperature"] == 0.9
    assert parsed.raw["openai_max_context"] == 8192


def test_unknown_fields_preserved(make_preset) -> None:
    """未知字段（社区私有扩展）原样保留，便于原样导出。"""
    parsed = parse_st_preset(make_preset(vendor_private_field={"nested": [1, 2]}))

    assert parsed.raw["vendor_private_field"] == {"nested": [1, 2]}


# --------------------------------------------------------------------------
# 未生效特性提示
# --------------------------------------------------------------------------


def test_text_completion_format_reported() -> None:
    """Text Completion 预设应被识别，并提示组装不适用。"""
    parsed = parse_st_preset({"temp": 0.8, "rep_pen": 1.1, "max_length": 300})

    assert parsed.source_format == "text"
    assert any("Text Completion" in note for note in parsed.unsupported)


def test_group_and_trigger_features_reported(make_preset) -> None:
    """群聊字段与不受支持的生成类型触发应进入未生效清单。"""
    data = make_preset(group_nudge_prompt="（测试语料）只以 {{char}} 身份回应。")
    _item(data, "reply-length")["injection_trigger"] = ["continue", "swipe"]

    parsed = parse_st_preset(data)

    assert any("group_nudge_prompt" in note for note in parsed.unsupported)
    assert any("continue" in note for note in parsed.unsupported)


def test_supported_trigger_not_reported(make_preset) -> None:
    """受支持的触发类型（normal/regenerate）不应产生未生效提示。"""
    data = make_preset()
    _item(data, "reply-length")["injection_trigger"] = ["normal", "regenerate"]

    parsed = parse_st_preset(data)

    assert parsed.unsupported == []


def test_continuation_fields_reported(make_preset) -> None:
    """续写/预填充相关设置应进入未生效清单（本项目无续写生成入口）。"""
    parsed = parse_st_preset(
        make_preset(
            continue_nudge_prompt="（测试语料）继续上一条。",
            assistant_prefill="（测试语料）预填充",
        )
    )

    assert any("续写" in note for note in parsed.unsupported)


# --------------------------------------------------------------------------
# 文本入口与错误处理
# --------------------------------------------------------------------------


def test_parse_text_tolerates_bom(make_preset) -> None:
    """带 UTF-8 BOM 的预设文件（Windows 记事本常见）应能解析。"""
    text = "\ufeff" + json.dumps(make_preset(), ensure_ascii=False)

    parsed = parse_st_preset_text(text)

    assert parsed.preset.temperature == 0.9
    assert len(parsed.preset.prompts) == 10


def test_invalid_input_raises() -> None:
    """非法输入应明确抛错（导入时据此不落盘）。"""
    with pytest.raises(ValueError):
        parse_st_preset_text("{ not json")
    with pytest.raises(ValueError):
        parse_st_preset(["not", "a", "dict"])   # type: ignore[arg-type]


def test_slugify_handles_ascii_and_non_ascii() -> None:
    """slug 化：ASCII 名称可用，纯符号名称返回空（由调用方兜底）。"""
    assert slugify("Reply Tone") == "reply-tone"
    assert slugify("Soft_Gentle Style") == "soft-gentle-style"
    assert slugify("！！！") == ""
