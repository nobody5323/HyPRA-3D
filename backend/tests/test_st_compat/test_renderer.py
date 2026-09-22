"""ST 预设组装渲染测试（夹具全部自创，见 conftest.py 的合规说明）。"""

from app.prompts.st_compat import parse_st_preset, render_st_preset


def _contents(rendered) -> list[str]:
    """消息正文列表（断言便利函数）。"""
    return [message["content"] for message in rendered.messages]


def _index_of(rendered, text: str) -> int:
    """某段正文在消息序列中的位置。"""
    return _contents(rendered).index(text)


# --------------------------------------------------------------------------
# 相对顺序与 marker 填充
# --------------------------------------------------------------------------


def test_relative_order_starts_with_main_and_skips_disabled(make_preset, make_context) -> None:
    """相对条目按顺序表展开，未启用条目跳过。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context())

    assert rendered.messages[0]["content"] == "（测试语料）请以角色身份回应对话者。"
    assert "（测试语料）每次回复控制在三句话以内。" not in _contents(rendered)   # reply-length 默认关闭


def test_markers_filled_from_context(make_preset, make_context) -> None:
    """marker 内容来自 HyPRA 运行时上下文，而不是预设。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context())

    assert "（测试语料）人设正文" in _contents(rendered)      # charDescription
    assert "（测试语料）世界书-前" in _contents(rendered)     # worldInfoBefore
    assert "charDescription" in rendered.used_markers
    assert "worldInfoBefore" in rendered.used_markers


def test_empty_markers_reported_without_empty_messages(make_preset, make_context) -> None:
    """没有内容来源的 marker 记入 empty_markers，且不产生空消息。"""
    context = make_context(persona_text="", worldbook_before="", worldbook_after="")

    rendered = render_st_preset(parse_st_preset(make_preset()), context)

    assert "charDescription" in rendered.empty_markers
    assert "worldInfoBefore" in rendered.empty_markers
    assert all(message["content"].strip() for message in rendered.messages)


def test_chat_history_sits_at_its_order_slot(make_preset, make_context) -> None:
    """历史块出现在 chatHistory 在顺序表中的位置（jailbreak 之后于它）。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context())

    assert _index_of(rendered, "（测试语料）你好") < _index_of(
        rendered, "（测试语料）保持体贴的语气，不要使用列表。"
    )


def test_history_appended_when_chat_history_disabled(make_preset, make_context) -> None:
    """预设未启用 chatHistory 槽位时，历史不能凭空消失（追加到末尾并告警）。"""
    data = make_preset()
    data["prompt_order"][0]["order"] = [
        entry for entry in data["prompt_order"][0]["order"] if entry["identifier"] != "chatHistory"
    ]

    rendered = render_st_preset(parse_st_preset(data), make_context())

    assert any("未启用 chatHistory" in warning for warning in rendered.warnings)
    assert "（测试语料）今天有点累" in _contents(rendered)


# --------------------------------------------------------------------------
# In-Chat 注入
# --------------------------------------------------------------------------


def test_in_chat_depth_counts_back_from_the_end(make_preset, make_context) -> None:
    """depth 2 = 从末尾往前数第 2 条之前（mood-note 夹具值）。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context())

    position = _index_of(rendered, "（测试语料）用户此刻情绪偏低落，回应放慢一些。")

    assert rendered.messages[position]["role"] == "user"
    assert rendered.messages[position + 1]["content"] == "（测试语料）我在"


def test_depth_zero_goes_after_user_input(make_preset, make_context) -> None:
    """depth 0 = 历史块内最后一条消息（本次输入）之后。"""
    data = make_preset()
    for item in data["prompts"]:
        if item["identifier"] == "mood-note":
            item["injection_depth"] = 0

    rendered = render_st_preset(parse_st_preset(data), make_context())
    position = _index_of(rendered, "（测试语料）今天有点累")

    assert rendered.messages[position + 1]["content"] == "（测试语料）用户此刻情绪偏低落，回应放慢一些。"


def test_same_depth_order_role_are_merged_into_one_message(make_preset, make_context) -> None:
    """同 depth + 同 order + 同 role 的多个条目合并成一条消息（换行连接）。"""
    data = make_preset()
    data["prompts"].append(
        {
            "identifier": "extra-note",
            "name": "附加提醒",
            "role": "user",
            "content": "（测试语料）附加提醒",
            "injection_position": 1,
            "injection_depth": 2,
            "injection_order": 50,
        }
    )
    data["prompt_order"][0]["order"].append({"identifier": "extra-note", "enabled": True})

    merged = next(
        message
        for message in render_st_preset(parse_st_preset(data), make_context()).messages
        if "附加提醒" in message["content"]
    )

    assert merged["content"] == "（测试语料）用户此刻情绪偏低落，回应放慢一些。\n（测试语料）附加提醒"
    assert merged["role"] == "user"


def test_different_roles_become_separate_messages_in_order(make_preset, make_context) -> None:
    """同 depth 同 order 但角色不同 → 按 system → user → assistant 顺序各出一条。"""
    data = make_preset()
    for item in data["prompts"]:
        if item["identifier"] == "mood-note":       # 夹具里 role=user
            item["injection_order"] = 100           # 与记忆扩展注入同层
    data["prompts"].append(
        {
            "identifier": "sys-note",
            "name": "系统提醒",
            "role": "system",
            "content": "（测试语料）系统提醒",
            "injection_position": 1,
            "injection_depth": 2,
            "injection_order": 100,
        }
    )
    data["prompt_order"][0]["order"].append({"identifier": "sys-note", "enabled": True})

    rendered = render_st_preset(
        parse_st_preset(data), make_context(), memory_injection={"position": "off"}
    )
    positions = [
        index
        for index, message in enumerate(rendered.messages)
        if message["content"] in ("（测试语料）系统提醒", "（测试语料）用户此刻情绪偏低落，回应放慢一些。")
    ]

    assert positions == sorted(positions)
    assert rendered.messages[positions[0]]["content"] == "（测试语料）系统提醒"
    assert rendered.messages[positions[0]]["role"] == "system"
    assert rendered.messages[positions[1]]["role"] == "user"


def test_in_chat_count_reported(make_preset, make_context) -> None:
    """注入条数统计（mood-note + 默认位置的记忆块）。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context())

    assert rendered.in_chat_count == 2


# --------------------------------------------------------------------------
# HyPRA 记忆层（扩展注入语义）
# --------------------------------------------------------------------------


def test_memory_injected_right_before_user_input(make_preset, make_context) -> None:
    """默认 IN_CHAT / depth=1 / system：紧邻本次输入之前。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context())

    position = _index_of(rendered, "（测试语料）记忆块")

    assert rendered.messages[position]["role"] == "system"
    assert rendered.messages[position + 1]["content"] == "（测试语料）今天有点累"


def test_memory_position_world_info_before(make_preset, make_context) -> None:
    """兼容模式：记忆并入 worldInfoBefore 槽位（与世界书同块注入）。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset()),
        make_context(),
        memory_injection={"position": "world_info_before"},
    )

    worldbook_message = next(
        message for message in rendered.messages if "（测试语料）世界书-前" in message["content"]
    )

    assert "（测试语料）记忆块" in worldbook_message["content"]


def test_memory_position_in_prompt_appends_at_end(make_preset, make_context) -> None:
    """in_prompt 模式等价于 ST 的 IN_PROMPT（放在相对序列末尾）。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset()),
        make_context(),
        memory_injection={"position": "in_prompt"},
    )

    assert rendered.messages[-1]["content"] == "（测试语料）记忆块"


def test_memory_can_be_switched_off(make_preset, make_context) -> None:
    """整体关闭记忆注入 = 纯预设行为。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset()), make_context(), memory_injection={"enabled": False}
    )

    assert "（测试语料）记忆块" not in _contents(rendered)
    assert rendered.in_chat_count == 1   # 只剩预设自带的 mood-note


def test_empty_memory_text_produces_no_message(make_preset, make_context) -> None:
    """记忆为空时不注入空消息。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context(memory_text=""))

    assert rendered.in_chat_count == 1
    assert all(message["content"].strip() for message in rendered.messages)


# --------------------------------------------------------------------------
# 组装控制开关
# --------------------------------------------------------------------------


def test_squash_system_messages_merges_neighbours(make_preset, make_context) -> None:
    """squash 开启时相邻 system 消息合并（main 与人设进同一条）。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset(squash_system_messages=True)), make_context()
    )
    first = rendered.messages[0]

    assert first["role"] == "system"
    assert "（测试语料）请以角色身份回应对话者。" in first["content"]
    assert "（测试语料）人设正文" in first["content"]


def test_without_squash_system_messages_stay_separate(make_preset, make_context) -> None:
    """squash 关闭时保持逐条消息。"""
    data = make_preset(squash_system_messages=False)

    rendered = render_st_preset(parse_st_preset(data), make_context())

    assert rendered.messages[0]["content"] == "（测试语料）请以角色身份回应对话者。"


def test_use_sysprompt_overrides_unless_forbidden(make_preset, make_context) -> None:
    """use_sysprompt 覆盖 system_prompt 条目，但 forbid_overrides 的条目豁免。"""
    data = make_preset(use_sysprompt=True, squash_system_messages=False)
    for item in data["prompts"]:
        if item["identifier"] == "jailbreak":
            item["forbid_overrides"] = True

    rendered = render_st_preset(
        parse_st_preset(data),
        make_context(system_prompt_override="（测试语料）统一系统指令"),
    )
    all_contents = "\n".join(_contents(rendered))

    assert "（测试语料）统一系统指令" in rendered.messages[0]["content"]
    assert "（测试语料）请以角色身份回应对话者。" not in all_contents   # main 被覆盖
    assert "（测试语料）保持体贴的语气，不要使用列表。" in all_contents             # jailbreak 豁免


def test_use_sysprompt_without_override_keeps_original(make_preset, make_context) -> None:
    """开启覆盖但没给覆盖文本时不把正文清空（容错，附告警）。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset(use_sysprompt=True)), make_context()
    )

    assert "（测试语料）请以角色身份回应对话者。" in _contents(rendered)
    assert any("未提供覆盖文本" in warning for warning in rendered.warnings)


def test_macro_last_messages_from_history(make_preset, make_context) -> None:
    """{{lastUserMessage}} / {{lastCharMessage}} 取自历史与本次输入。"""
    from app.session.context import ChatTurn

    data = make_preset()
    for item in data["prompts"]:
        if item["identifier"] == "main":
            item["content"] = "上一句：{{lastcharmessage}} / 我说：{{lastusermessage}}"

    rendered = render_st_preset(
        parse_st_preset(data),
        make_context(
            history=[ChatTurn("assistant", "我在听")],
            user_input="今天有点累",
        ),
    )

    assert rendered.messages[0]["content"] == "上一句：我在听 / 我说：今天有点累"
    assert rendered.unresolved_macros == []


def test_dialogue_examples_block_with_separator(make_preset, make_context) -> None:
    """示例块：示例分隔文本 + 示例对话，插在 dialogueExamples 槽位。"""
    data = make_preset(new_example_chat_prompt="（测试语料）示例开始")

    rendered = render_st_preset(parse_st_preset(data), make_context())
    contents = _contents(rendered)
    start = contents.index("（测试语料）示例开始")

    assert contents[start + 1] == "（测试语料）示例提问"
    assert contents[start + 2] == "（测试语料）示例回应"
    assert rendered.example_count == 1


def test_names_behavior_prefixes_history(make_preset, make_context) -> None:
    """names_behavior=1 时历史消息带「角色名: 」前缀。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset(names_behavior=1)), make_context()
    )
    contents = _contents(rendered)

    assert "朋友: （测试语料）你好" in contents
    assert "苏澄: （测试语料）我在" in contents


def test_names_behavior_zero_keeps_raw_text(make_preset, make_context) -> None:
    """names_behavior=0 时不加前缀。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset(names_behavior=0)), make_context()
    )
    contents = _contents(rendered)

    assert "（测试语料）你好" in contents
    assert "朋友: （测试语料）你好" not in contents


def test_new_chat_prompt_only_when_history_empty(make_preset, make_context) -> None:
    """新会话分隔文本仅在历史为空时插入。"""
    data = make_preset(new_chat_prompt="（测试语料）新对话开始")

    with_history = render_st_preset(parse_st_preset(data), make_context())
    without_history = render_st_preset(parse_st_preset(data), make_context(history=[]))

    assert "（测试语料）新对话开始" not in _contents(with_history)
    assert "（测试语料）新对话开始" in _contents(without_history)


def test_send_if_empty_used_for_blank_input(make_preset, make_context) -> None:
    """空输入时用预设的替代文本，并记告警。"""
    data = make_preset(send_if_empty="（测试语料）空消息占位")

    rendered = render_st_preset(parse_st_preset(data), make_context(user_input=""))

    assert "（测试语料）空消息占位" in _contents(rendered)
    assert any("空消息替代文本" in warning for warning in rendered.warnings)


def test_blank_input_without_send_if_empty_adds_no_message(make_preset, make_context) -> None:
    """既无输入也无替代文本时不产生空 user 消息。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context(user_input=""))

    assert all(message["content"].strip() for message in rendered.messages)
    assert rendered.messages[-1]["content"] != ""


# --------------------------------------------------------------------------
# 生成类型触发与格式模板
# --------------------------------------------------------------------------


def test_injection_trigger_filters_by_generation_type(make_preset, make_context) -> None:
    """条目限定的生成类型与当前类型不符时不发送。"""
    data = make_preset()
    for item in data["prompts"]:
        if item["identifier"] == "main":
            item["injection_trigger"] = ["regenerate"]

    normal = render_st_preset(parse_st_preset(data), make_context(generation_type="normal"))
    regenerate = render_st_preset(
        parse_st_preset(data), make_context(generation_type="regenerate")
    )

    assert "（测试语料）请以角色身份回应对话者。" not in _contents(normal)
    assert "（测试语料）请以角色身份回应对话者。" in _contents(regenerate)


def test_wi_format_wraps_content(make_preset, make_context) -> None:
    """wi_format 模板按占位符包装世界书内容。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset(wi_format="<世界书>{0}</世界书>")), make_context()
    )

    assert "<世界书>（测试语料）世界书-前</世界书>" in _contents(rendered)


def test_format_template_without_placeholder_keeps_content(make_preset, make_context) -> None:
    """模板缺占位符时保留内容并告警（ST 在同样情况下会静默丢内容）。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset(wi_format="没有占位符的包装")), make_context()
    )

    assert "（测试语料）世界书-前" in _contents(rendered)
    assert any("缺少占位符" in warning for warning in rendered.warnings)


def test_scenario_and_personality_formats(make_preset, make_context) -> None:
    """场景与性格模板同样支持占位符替换。"""
    rendered = render_st_preset(
        parse_st_preset(
            make_preset(
                scenario_format="[场景]{{scenario}}",
                personality_format="[性格]{{personality}}",
            )
        ),
        make_context(),
    )
    contents = _contents(rendered)

    assert "[场景]（测试语料）场景" in contents
    assert "[性格]（测试语料）性格标签" in contents


# --------------------------------------------------------------------------
# 结果结构
# --------------------------------------------------------------------------


def test_system_prompt_property_collects_system_messages(make_preset, make_context) -> None:
    """system_prompt 属性汇总全部 system 消息（调试/预览用）。"""
    rendered = render_st_preset(parse_st_preset(make_preset()), make_context())

    assert "（测试语料）请以角色身份回应对话者。" in rendered.system_prompt


def test_preset_warnings_are_carried_into_result(make_preset, make_context) -> None:
    """解析阶段的警告要一并带出（界面上一次看全）。"""
    data = make_preset(prompt_order=[])

    parsed = parse_st_preset(data)
    rendered = render_st_preset(parsed, make_context())

    assert any("未提供条目顺序" in warning for warning in rendered.warnings)


# --------------------------------------------------------------------------
# 文风层（HyPRA 自有层在 ST 布局中的位置，契约文档 §6.6）
# --------------------------------------------------------------------------


def test_style_text_appended_to_jailbreak(make_preset, make_context) -> None:
    """文风块追加到 jailbreak 位，不覆盖预设正文。"""
    rendered = render_st_preset(
        parse_st_preset(make_preset()), make_context(style_text="（测试语料）文风约束")
    )
    jailbreak_message = next(
        message
        for message in rendered.messages
        if "（测试语料）保持体贴的语气，不要使用列表。" in message["content"]
    )

    assert "（测试语料）文风约束" in jailbreak_message["content"]
    assert jailbreak_message["content"].startswith("（测试语料）保持体贴的语气")


def test_style_text_falls_back_to_tail_when_jailbreak_missing(make_preset, make_context) -> None:
    """预设没启用 jailbreak 槽位时，文风块追加到末尾并告警。"""
    data = make_preset()
    data["prompt_order"][0]["order"] = [
        entry for entry in data["prompt_order"][0]["order"] if entry["identifier"] != "jailbreak"
    ]

    rendered = render_st_preset(
        parse_st_preset(data), make_context(style_text="（测试语料）文风约束")
    )

    assert rendered.messages[-1]["content"] == "（测试语料）文风约束"
    assert any("jailbreak" in warning for warning in rendered.warnings)
