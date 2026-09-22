"""ST 预设兼容的 API 与端到端测试（导入 → 编辑 → 组装 → 导出）。

合规（AGENTS.md §6）：夹具预设**全部自写**（结构对齐酒馆预设、正文为测试语料），
不复制 SillyTavern 或任何社区预设的提示词原文。
"""

import json

from fastapi.testclient import TestClient

from app.api import chat as chat_module
from app.main import app

client = TestClient(app)

IMPORT_URL = "/chat/st-presets/import"


def make_preset(**overrides) -> dict:
    """自造脱敏 ST 预设（含 1 个 marker、1 个普通条目、1 个 In-Chat 条目）。"""
    preset = {
        # ---- 采样与组装控制 ----
        "temperature": 0.66,
        "top_p": 0.9,
        "openai_max_tokens": 256,
        "top_k": 40,                       # 扩展参数：只保存与展示，不透传
        "names_behavior": 0,
        "squash_system_messages": False,
        # ---- 端点/密钥类字段（导入时应被剥离且不落盘）----
        "chat_completion_source": "openai",
        "custom_url": "https://example.invalid/v1",
        "proxy_password": "sk-must-not-be-stored",
        "prompts": [
            {
                "identifier": "main",
                "name": "Main",
                "system_prompt": True,
                "role": "system",
                "content": "（测试语料）以 {{char}} 的身份回应 {{user}}。",
            },
            {"identifier": "worldInfoBefore", "name": "WI before", "system_prompt": True, "marker": True},
            {"identifier": "charDescription", "name": "Char Desc", "system_prompt": True, "marker": True},
            {"identifier": "chatHistory", "name": "Chat History", "system_prompt": True, "marker": True},
            {
                "identifier": "jailbreak",
                "name": "Post-History",
                "system_prompt": True,
                "role": "system",
                "content": "（测试语料）保持温柔语气。",
            },
            {
                "identifier": "mood-note",
                "name": "情绪提醒",
                "role": "user",
                "content": "（测试语料）回应放慢一些。",
                "injection_position": 1,
                "injection_depth": 1,
                "injection_order": 100,
            },
        ],
        "prompt_order": [
            {
                "character_id": 100000,
                "order": [
                    {"identifier": "main", "enabled": True},
                    {"identifier": "worldInfoBefore", "enabled": True},
                    {"identifier": "charDescription", "enabled": True},
                    {"identifier": "chatHistory", "enabled": True},
                    {"identifier": "jailbreak", "enabled": True},
                    {"identifier": "mood-note", "enabled": True},
                ],
            }
        ],
    }
    preset.update(overrides)
    return preset


def import_preset(name: str = "tavern-preset", as_object: bool = False, **overrides):
    """导入一份自造预设，返回响应。"""
    preset = make_preset(**overrides)
    content = preset if as_object else json.dumps(preset, ensure_ascii=False)
    return client.post(
        IMPORT_URL,
        json={"content": content, "source_file": f"{name}.json"},
    )


# --------------------------------------------------------------------------
# 导入 / 清单 / 详情
# --------------------------------------------------------------------------


def test_import_and_list_preset() -> None:
    """导入后清单与详情可用，且清单不含正文。"""
    resp = import_preset()
    assert resp.status_code == 200
    body = resp.json()

    assert body["preset"]["id"] == "tavern-preset"
    assert body["preset"]["prompt_count"] == 6
    assert body["detail"]["sampling"]["temperature"] == 0.66
    assert body["detail"]["extended_sampling"]["top_k"] == 40

    listing = client.get("/chat/st-presets").json()
    assert [item["id"] for item in listing["presets"]] == ["tavern-preset"]
    assert "content" not in json.dumps(listing, ensure_ascii=False)   # 清单不含正文

    detail = client.get("/chat/st-presets/tavern-preset").json()
    identifiers = [item["identifier"] for item in detail["detail"]["prompts"]]
    assert identifiers[0] == "main"
    assert detail["detail"]["prompts"][0]["content_editable"] is True
    chat_history = next(
        item for item in detail["detail"]["prompts"] if item["identifier"] == "chatHistory"
    )
    assert chat_history["marker"] is True
    assert chat_history["content_editable"] is False       # 占位条目正文不可编辑
    assert chat_history["marker_source"]                  # 界面可展示内容来源


def test_import_accepts_object_content() -> None:
    """content 既接受 JSON 文本，也接受已解析对象。"""
    resp = import_preset(as_object=True)
    assert resp.status_code == 200
    assert resp.json()["preset"]["id"] == "tavern-preset"


def test_import_invalid_json_is_400_and_not_stored() -> None:
    """解析失败必须 400，且不落盘、不出现在清单里。"""
    resp = client.post(IMPORT_URL, json={"content": "{ not json", "source_file": "broken.json"})
    assert resp.status_code == 400
    assert client.get("/chat/st-presets").json()["presets"] == []


def test_import_does_not_persist_credentials() -> None:
    """端点/密钥类字段剥离且不落盘（安全红线）。"""
    import_preset()
    store = chat_module.get_st_preset_store()
    raw = (store.root / "tavern-preset.json").read_text(encoding="utf-8")

    assert "sk-must-not-be-stored" not in raw
    assert "proxy_password" not in raw
    assert "custom_url" not in raw
    assert "temperature" in raw


def test_unknown_preset_is_404() -> None:
    assert client.get("/chat/st-presets/nope").status_code == 404
    assert client.patch("/chat/st-presets/nope", json={"sampling": {"temperature": 0.5}}).status_code == 404
    assert client.delete("/chat/st-presets/nope").status_code == 404


# --------------------------------------------------------------------------
# 覆盖层编辑
# --------------------------------------------------------------------------


def test_patch_override_toggles_entry_and_updates_sampling() -> None:
    """PATCH 写入覆盖层：采样参数与条目开关生效。"""
    import_preset()

    resp = client.patch(
        "/chat/st-presets/tavern-preset",
        json={
            "sampling": {"temperature": 0.2},
            "prompts": {"mood-note": {"enabled": False}},
        },
    )
    assert resp.status_code == 200

    detail = resp.json()["detail"]
    assert detail["sampling"]["temperature"] == 0.2
    entry = next(e for e in detail["order"] if e["identifier"] == "mood-note")
    assert entry["enabled"] is False
    assert client.get("/chat/st-presets").json()["presets"][0]["has_override"] is True


def test_patch_marker_content_is_ignored() -> None:
    """占位条目的正文不可被覆盖（内容由运行时填充）。"""
    import_preset()

    resp = client.patch(
        "/chat/st-presets/tavern-preset",
        json={"prompts": {"chatHistory": {"content": "（测试语料）不该生效"}}},
    )
    assert resp.status_code == 200

    detail = resp.json()["detail"]
    chat_history = next(i for i in detail["prompts"] if i["identifier"] == "chatHistory")
    assert chat_history["content"] == ""
    assert any("占位条目" in w for w in detail["warnings"])


def test_patch_reorders_entries() -> None:
    """PATCH 的 prompt_order 决定新顺序（未提及的条目追加到末尾）。"""
    import_preset()

    resp = client.patch(
        "/chat/st-presets/tavern-preset",
        json={"prompt_order": ["jailbreak", "main", "chatHistory"]},
    )
    order = [e["identifier"] for e in resp.json()["detail"]["order"]]

    assert order[:3] == ["jailbreak", "main", "chatHistory"]
    assert set(order) == {"main", "worldInfoBefore", "charDescription", "chatHistory", "jailbreak", "mood-note"}


def test_patch_memory_injection() -> None:
    """记忆注入配置可编辑，且默认值符合契约（IN_CHAT / depth=1 / system）。"""
    import_preset()
    default = client.get("/chat/st-presets/tavern-preset").json()["detail"]["memory_injection"]
    assert default == {
        "enabled": True,
        "position": "in_chat",
        "depth": 1,
        "role": "system",
        "order": 100,
    }

    patched = client.patch(
        "/chat/st-presets/tavern-preset",
        json={"memory_injection": {"depth": 3, "position": "off"}},
    ).json()["detail"]["memory_injection"]

    assert patched["depth"] == 3
    assert patched["position"] == "off"
    assert patched["role"] == "system"      # 未覆盖项保持默认


def test_patch_empty_body_is_400() -> None:
    import_preset()
    assert client.patch("/chat/st-presets/tavern-preset", json={}).status_code == 400


def test_patch_only_updates_provided_fields() -> None:
    """PATCH 语义：未提供的字段不受影响（用 exclude_unset 取补丁）。"""
    import_preset()
    client.patch("/chat/st-presets/tavern-preset", json={"sampling": {"temperature": 0.2}})
    client.patch("/chat/st-presets/tavern-preset", json={"prompts": {"main": {"enabled": False}}})

    detail = client.get("/chat/st-presets/tavern-preset").json()["detail"]

    assert detail["sampling"]["temperature"] == 0.2        # 未被第二次 PATCH 清掉
    assert detail["sampling"]["top_p"] == 0.9              # 预设原值仍在


def test_patch_null_restores_preset_value() -> None:
    """值为 null = 删除该覆盖项（回到预设原值），否则调过就再也回不到导入时。"""
    import_preset()
    client.patch("/chat/st-presets/tavern-preset", json={"sampling": {"temperature": 0.2}})
    assert (
        client.get("/chat/st-presets/tavern-preset").json()["detail"]["sampling"]["temperature"]
        == 0.2
    )

    resp = client.patch(
        "/chat/st-presets/tavern-preset", json={"sampling": {"temperature": None}}
    )

    assert resp.status_code == 200
    assert resp.json()["detail"]["sampling"]["temperature"] == 0.66


def test_patch_assembly_switches() -> None:
    """组装开关可编辑（合并系统消息 / 历史角色名前缀）。"""
    import_preset()

    resp = client.patch(
        "/chat/st-presets/tavern-preset",
        json={"assembly": {"squash_system_messages": True, "names_behavior": 1}},
    )
    assert resp.status_code == 200
    assembly = resp.json()["detail"]["assembly"]
    assert assembly["squash_system_messages"] is True
    assert assembly["names_behavior"] == 1

    # 非法枚举值必须**拒收而不是忽略**：覆盖层是深合并的，
    # 存下非法值会把上一次的合法值顶掉（结果反而退回预设原值）
    resp2 = client.patch(
        "/chat/st-presets/tavern-preset", json={"assembly": {"names_behavior": 9}}
    )
    assert resp2.status_code == 400
    detail = client.get("/chat/st-presets/tavern-preset").json()["detail"]
    assert detail["assembly"]["names_behavior"] == 1


def test_patch_rejects_illegal_role() -> None:
    """条目角色是枚举，非法值同样在写入前拒收。"""
    import_preset()

    resp = client.patch(
        "/chat/st-presets/tavern-preset", json={"prompts": {"main": {"role": "wizard"}}}
    )

    assert resp.status_code == 400
    detail = client.get("/chat/st-presets/tavern-preset").json()["detail"]
    assert next(i for i in detail["prompts"] if i["identifier"] == "main")["role"] == "system"


def test_assembly_switch_affects_rendering() -> None:
    """组装的开关真地影响下一轮 messages（不只是回显）。"""
    import_preset()
    client.patch(
        "/chat/st-presets/tavern-preset",
        json={"assembly": {"squash_system_messages": True}},
    )

    body = client.post(
        "/chat", json={"text": "你好", "st_preset_id": "tavern-preset"}
    ).json()
    system_messages = [m for m in body["messages"] if m["role"] == "system"]

    # main / charDescription / jailbreak 都是 system，squash 后应合并成更少的消息
    assert len(system_messages) < 3


def test_reset_override_restores_import_state() -> None:
    """重置后回到导入时状态（原始文件始终只读）。"""
    import_preset()
    client.patch("/chat/st-presets/tavern-preset", json={"sampling": {"temperature": 0.1}})

    resp = client.post("/chat/st-presets/tavern-preset/reset")
    assert resp.status_code == 200
    assert resp.json()["detail"]["sampling"]["temperature"] == 0.66
    assert client.get("/chat/st-presets").json()["presets"][0]["has_override"] is False


def test_delete_preset() -> None:
    import_preset()
    assert client.delete("/chat/st-presets/tavern-preset").json() == {"deleted": "tavern-preset"}
    assert client.get("/chat/st-presets").json()["presets"] == []
    assert client.get("/chat/st-presets/tavern-preset").status_code == 404


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------


def test_export_roundtrip() -> None:
    """导出的 JSON 是 ST 兼容结构，可原样再导入。"""
    import_preset()
    client.patch("/chat/st-presets/tavern-preset", json={"sampling": {"temperature": 0.31}})

    exported = client.get("/chat/st-presets/tavern-preset/export").json()
    assert exported["temperature"] == 0.31
    assert exported["openai_max_tokens"] == 256
    assert isinstance(exported["prompts"], list)

    again = client.post(
        IMPORT_URL,
        json={
            "content": json.dumps(exported, ensure_ascii=False),
            "source_file": "roundtrip.json",
        },
    )
    assert again.status_code == 200
    assert again.json()["detail"]["sampling"]["temperature"] == 0.31


def test_export_without_override_returns_raw() -> None:
    import_preset()
    client.patch("/chat/st-presets/tavern-preset", json={"sampling": {"temperature": 0.31}})

    raw = client.get(
        "/chat/st-presets/tavern-preset/export", params={"apply_override": False}
    ).json()

    assert raw["temperature"] == 0.66


# --------------------------------------------------------------------------
# 端到端：用 ST 预设跑一轮对话
# --------------------------------------------------------------------------


def test_chat_without_st_preset_keeps_builtin_path() -> None:
    """回归：不传 st_preset_id 时仍走内置分层路径。"""
    body = client.post("/chat", json={"text": "我失眠了"}).json()

    assert body["system_prompt"].startswith("[角色人设]")
    assert body["st_preset"] == {}
    assert "内置分层" in body["note"]


def test_chat_with_st_preset_uses_preset_order() -> None:
    """选定 ST 预设后：消息顺序、宏替换、marker 填充均按预设语义。"""
    import_preset()

    body = client.post(
        "/chat",
        json={"text": "今天有点累", "st_preset_id": "tavern-preset", "user_name": "小林"},
    ).json()

    # ① 预设正文里的宏按上下文替换
    assert body["messages"][0]["content"] == "（测试语料）以 苏澄 的身份回应 小林。"
    # ② marker 由 HyPRA 内容填充（charDescription ← 人设正文）
    assert "心理咨询" in body["system_prompt"]
    # ③ 顺序按 prompt_order：jailbreak 在 chatHistory 之后
    contents = [m["content"] for m in body["messages"]]
    jailbreak_index = next(
        i for i, text in enumerate(contents) if "（测试语料）保持温柔语气。" in text
    )
    assert jailbreak_index > contents.index("今天有点累")
    # ④ 元信息与说明
    assert body["st_preset"]["st_preset_id"] == "tavern-preset"
    assert body["st_preset"]["used_markers"]
    assert body["st_preset"]["unresolved_macros"] == []
    assert "酒馆预设 tavern-preset" in body["note"]


def test_chat_st_preset_participates_in_sampling_merge() -> None:
    """采样三级合并（契约 §10）：内置档 → ST 预设 → 文风预设。

    夹具的文风预设也设了 temperature，按约定它优先级最高；
    未被文风覆盖的项（top_p）应来自 ST 预设。
    """
    import_preset()

    body = client.post(
        "/chat", json={"text": "你好", "st_preset_id": "tavern-preset"}
    ).json()
    sources = body["preset"]["sources"]

    assert body["preset"]["top_p"] == 0.9
    assert sources["top_p"] == "st:tavern-preset"
    assert sources["temperature"] == "style"        # 文风优先级最高
    # 扩展参数（top_k）只保存展示，不得混进实际生效参数
    assert "top_k" not in sources


def test_chat_with_style_disabled_lets_preset_sampling_win() -> None:
    """style_id="none" 关闭文风层：ST 预设的采样参数与正文完全生效。"""
    import_preset()

    body = client.post(
        "/chat",
        json={"text": "你好", "st_preset_id": "tavern-preset", "style_id": "none"},
    ).json()

    assert body["preset"]["temperature"] == 0.66
    assert body["preset"]["max_tokens"] == 256
    assert body["preset"]["sources"]["temperature"] == "st:tavern-preset"
    assert body["style"] == {}                       # 文风层未启用
    assert not any("表达风格" in m["content"] for m in body["messages"])


def test_chat_st_preset_injects_memory_as_extension() -> None:
    """HyPRA 记忆层以扩展注入参与 ST 布局（与同层同 order 的预设条目合并）。"""
    import_preset()
    # 先写入一条情景记忆，保证下一轮有召回内容
    first = client.post(
        "/chat",
        json={"text": "我最怕打雷，会躲进被子", "st_preset_id": "tavern-preset"},
    ).json()
    sid = first["session_id"]

    second = client.post(
        "/chat",
        json={"text": "今天又打雷了", "session_id": sid, "st_preset_id": "tavern-preset"},
    ).json()

    messages = second["messages"]
    assert second["st_preset"]["in_chat_count"] >= 2      # 预设的 In-Chat 条目 + 记忆块
    assert any("[记忆回忆]" in m["content"] for m in messages)
    # 记忆块与预设 In-Chat 条目同 depth / 同 order，按 ST 规则同层合并（system 在前）
    memory_index = next(i for i, m in enumerate(messages) if "[记忆回忆]" in m["content"])
    assert messages[memory_index]["role"] == "system"
    assert messages[memory_index + 1]["content"] == "（测试语料）回应放慢一些。"
    # 两者都在本次输入之前（depth 1 = 从末尾往前数第 1 条之前）
    assert messages[memory_index + 2]["content"] == "今天又打雷了"


def test_chat_unknown_st_preset_is_404() -> None:
    resp = client.post("/chat", json={"text": "你好", "st_preset_id": "no-such-preset"})
    assert resp.status_code == 404


def test_st_macro_variables_persist_across_turns() -> None:
    """{{setvar::}} 写入会话（mv: 前缀），下一轮 {{getvar::}} 能读到。"""
    import_preset(
        prompts=[
            {
                "identifier": "main",
                "name": "Main",
                "system_prompt": True,
                "role": "system",
                "content": "{{setvar::mood::低落}}（测试语料）开场。",
            },
            {"identifier": "chatHistory", "name": "Chat History", "system_prompt": True, "marker": True},
            {
                "identifier": "jailbreak",
                "name": "Post-History",
                "system_prompt": True,
                "role": "system",
                "content": "心情：{{getvar::mood}}",
            },
        ],
        prompt_order=[
            {
                "character_id": 100000,
                "order": [
                    {"identifier": "main", "enabled": True},
                    {"identifier": "chatHistory", "enabled": True},
                    {"identifier": "jailbreak", "enabled": True},
                ],
            }
        ],
    )

    first = client.post(
        "/chat", json={"text": "你好", "st_preset_id": "tavern-preset"}
    ).json()
    sid = first["session_id"]
    assert any("心情：低落" in m["content"] for m in first["messages"])

    # 宏变量已按会话落库
    session = chat_module.get_session_repository().get(sid)
    assert session.state_vars.get("mv:mood") == "低落"

    # 第二轮（新会话的另一轮）仍能读到
    second = client.post(
        "/chat",
        json={"text": "还在吗", "session_id": sid, "st_preset_id": "tavern-preset"},
    ).json()
    assert any("心情：低落" in m["content"] for m in second["messages"])


def test_st_preset_entry_toggle_affects_next_turn() -> None:
    """界面上关掉某个条目后，下一轮该条目不再出现。"""
    import_preset()
    client.patch(
        "/chat/st-presets/tavern-preset",
        json={"prompts": {"jailbreak": {"enabled": False}}},
    )

    body = client.post(
        "/chat", json={"text": "你好", "st_preset_id": "tavern-preset"}
    ).json()

    assert not any(
        "（测试语料）保持温柔语气。" in m["content"] for m in body["messages"]
    )


def test_st_preset_body_edit_affects_next_turn() -> None:
    """正文编辑（写覆盖层）在下一轮生效，且不修改原始文件。"""
    import_preset()
    client.patch(
        "/chat/st-presets/tavern-preset",
        json={"prompts": {"jailbreak": {"content": "（测试语料）换成简短语气。"}}},
    )

    body = client.post(
        "/chat", json={"text": "你好", "st_preset_id": "tavern-preset"}
    ).json()

    contents = [m["content"] for m in body["messages"]]
    assert any("（测试语料）换成简短语气。" in text for text in contents)
    assert not any("（测试语料）保持温柔语气。" in text for text in contents)

    raw = (chat_module.get_st_preset_store().root / "tavern-preset.json").read_text(
        encoding="utf-8"
    )
    assert "保持温柔语气" in raw          # 原始文件未被改写
