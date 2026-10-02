"""ST 预设兼容的 API 与端到端测试（导入 → 编辑 → 组装 → 导出）。

合规（AGENTS.md §6）：夹具预设**全部自写**（结构对齐酒馆预设、正文为测试语料），
不复制 SillyTavern 或任何社区预设的提示词原文。
"""

import json

from fastapi.testclient import TestClient

from app.api import chat as chat_module
from app.api import st_presets as st_presets_module
from app.llm.base import ChatMessage, LLMProvider
from app.main import app
from app.rag.prompt_manager import DIALOGUE_ONLY_RULE, PERCEPTION_SECTION

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
    """端点/密钥类字段剥离且不落盘（安全红线），并向界面报告剥了什么。"""
    import_preset()
    store = chat_module.get_st_preset_store()
    raw = (store.root / "tavern-preset.json").read_text(encoding="utf-8")

    assert "sk-must-not-be-stored" not in raw
    assert "proxy_password" not in raw
    assert "custom_url" not in raw
    assert "temperature" in raw

    # 剥离只发生在导入那一刻：详情必须从索引读回字段名，否则界面上看不到这个提示
    detail = client.get("/chat/st-presets/tavern-preset").json()["detail"]
    assert "proxy_password" in detail["stripped_keys"]
    assert "custom_url" in detail["stripped_keys"]


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


def test_patch_system_prompt_override_affects_rendering() -> None:
    """use_sysprompt 开启时，覆盖文本会替换系统条目正文（可在界面上编辑）。"""
    import_preset(use_sysprompt=True)

    resp = client.patch(
        "/chat/st-presets/tavern-preset",
        json={"system_prompt_override": "（测试语料）统一系统指令"},
    )
    assert resp.status_code == 200
    assert resp.json()["detail"]["system_prompt_override"] == "（测试语料）统一系统指令"

    body = client.post(
        "/chat", json={"text": "你好", "st_preset_id": "tavern-preset"}
    ).json()
    all_text = "\n".join(message["content"] for message in body["messages"])

    assert "（测试语料）统一系统指令" in all_text
    assert "（测试语料）以角色身份回应对话者。" not in all_text   # main 被替换
    assert "（测试语料）保持温柔语气。" not in all_text            # jailbreak 也被替换


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

    # 末尾那条是产品级的输出形态约束（渲染之后追加，见 `_assemble_with_st_preset`），
    # 它不属于预设自身条目、也不参与预设的 squash，所以统计时要排除；
    # 感知层（`[此刻]`，含「现在几点 / 最近在做什么」）同理——它由宿主注入，
    # 不是预设条目，也与 squash 无关。
    preset_system = [
        message
        for message in system_messages
        if DIALOGUE_ONLY_RULE not in message["content"]
        and not message["content"].startswith(f"[{PERCEPTION_SECTION}]")
    ]

    # main / charDescription / jailbreak 都是 system，squash 后应合并成更少的消息
    assert len(preset_system) < 3


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


# --------------------------------------------------------------------------
# AI 适配（导入后一键改造成陪伴对话形态）
# --------------------------------------------------------------------------

ADAPT_PRESET = "adapt-preset"
ADAPT_MAIN = "（测试语料）以角色身份用第二人称回应对话者。"
ADAPT_JAILBREAK = "（测试语料）NSFW核心：描写务必直白。允许破限题材。"


class FakeLLM(LLMProvider):
    """测试替身：返回构造好的文本（或抛异常），并记录收到的消息。"""

    name = "fake"

    def __init__(self, reply: str | Exception = "") -> None:
        self.reply = reply
        self.calls: list[list[ChatMessage]] = []

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> str:
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def make_adapt_preset(**overrides) -> dict:
    """构造一份「带适配问题」的自造预设。

    三类症状各一份：推理模式 + 超大长度上限（慢）、第三人称准则（人称）、
    NSFW / 破限条目（内容須原样保留）。
    """
    preset = make_preset(show_thoughts=True, openai_max_tokens=8192, **overrides)
    preset["prompts"][0]["content"] = "（测试语料）# 人称准则：全程务必采用第三人称创作。"
    preset["prompts"][4]["content"] = ADAPT_JAILBREAK      # jailbreak 槽位
    return preset


def import_adapt_preset(name: str = ADAPT_PRESET):
    """导入适配测试用预设。"""
    return client.post(
        IMPORT_URL,
        json={"content": make_adapt_preset(), "source_file": f"{name}.json"},
    )


def adapt(preset_id: str = ADAPT_PRESET, **payload):
    """调用 AI 适配端点。"""
    return client.post(f"/chat/st-presets/{preset_id}/ai-adapt", json=payload)


def rewrite_reply(identifier: str, content: str) -> str:
    """模拟模型返回的 JSON。"""
    return json.dumps(
        {"items": [{"identifier": identifier, "content": content}]}, ensure_ascii=False
    )


def test_ai_adapt_dry_run_returns_plan_without_writing() -> None:
    """默认 dry_run：给出建议但不写覆盖层。"""
    import_adapt_preset()

    body = adapt().json()

    assert body["dry_run"] is True
    assert body["applied"] is False
    assert body["summary"]["high"] >= 1
    assert body["patch"]["assembly"]["show_thoughts"] is False
    assert "detail" not in body                                # 未落盘就没必要回传详情
    assert chat_module.get_st_preset_store().get_override(ADAPT_PRESET) == {}


def test_ai_adapt_apply_writes_override_and_can_reset() -> None:
    """dry_run=false 才落盘；且随时能一键回到导入时的状态。"""
    import_adapt_preset()

    body = adapt(dry_run=False).json()

    assert body["applied"] is True
    assert body["detail"]["assembly"]["show_thoughts"] is False
    assert body["detail"]["sampling"]["max_tokens"] == 1024

    chat_module.get_st_preset_store().reset_override(ADAPT_PRESET)
    detail = client.get(f"/chat/st-presets/{ADAPT_PRESET}").json()["detail"]
    assert detail["assembly"]["show_thoughts"] is True
    assert detail["sampling"]["max_tokens"] == 8192


def test_ai_adapt_calls_model_once_and_applies_rewrite() -> None:
    """条目语义改写只调一次模型，结果并入同一个补丁。"""
    import_adapt_preset()
    fake = FakeLLM(rewrite_reply("main", ADAPT_MAIN))
    chat_module.set_llm_provider(fake)

    body = adapt(dry_run=False).json()

    assert len(fake.calls) == 1
    assert body["model_used"] is True
    assert body["patch"]["prompts"]["main"]["content"] == ADAPT_MAIN
    assert body["patch"]["assembly"]["show_thoughts"] is False      # 确定性修复没被覆盖
    outcome = next(item for item in body["rewrites"] if item["identifier"] == "main")
    assert outcome["status"] == "applied"


def test_ai_adapt_can_skip_model() -> None:
    """use_model=false：只做确定性修复，完全不碰模型。"""
    import_adapt_preset()
    fake = FakeLLM(rewrite_reply("main", ADAPT_MAIN))
    chat_module.set_llm_provider(fake)

    body = adapt(use_model=False).json()

    assert fake.calls == []
    assert body["model_used"] is False
    assert body["patch"]["assembly"]["show_thoughts"] is False
    assert all(item["status"] == "skipped" for item in body["rewrites"])


def test_ai_adapt_preserves_nsfw_and_jailbreak_content() -> None:
    """NSFW / 破限条目内容不动，也不进模型改写清单。"""
    import_adapt_preset()

    body = adapt().json()

    patched = (body["patch"].get("prompts") or {}).get("jailbreak") or {}
    assert "content" not in patched
    assert all(item["identifier"] != "jailbreak" for item in body["rewrites"])
    assert any(item["identifier"] == "jailbreak" for item in body["preserved"])


def test_ai_adapt_degrades_when_model_unavailable(monkeypatch) -> None:
    """模型不可用时仍交付确定性修复，不报错。"""
    import_adapt_preset()
    monkeypatch.setattr(st_presets_module, "_llm_provider_resolver", lambda: None)

    body = adapt().json()

    assert body["model_used"] is False
    assert body["patch"]["sampling"]["openai_max_tokens"] == 1024
    assert any("未配置可用模型" in warning for warning in body["warnings"])


def test_ai_adapt_survives_model_failure() -> None:
    """模型抛异常时降级，不得 500。"""
    import_adapt_preset()
    chat_module.set_llm_provider(FakeLLM(RuntimeError("（测试语料）连接超时")))

    resp = adapt()

    assert resp.status_code == 200
    body = resp.json()
    assert body["model_used"] is False
    assert any("模型调用失败" in warning for warning in body["warnings"])


def test_ai_adapt_diff_carries_before_and_after() -> None:
    """对照项要带字段名与前后值，供界面直接渲染。"""
    import_adapt_preset()

    body = adapt().json()

    thinking = next(item for item in body["diff"] if item["field"] == "show_thoughts")
    assert thinking["scope"] == "preset"
    assert thinking["before"] is True
    assert thinking["after"] is False
    assert thinking["field_label"] == "推理模式"

    tokens = next(item for item in body["diff"] if item["field"] == "openai_max_tokens")
    assert tokens["before"] == 8192
    assert tokens["after"] == 1024


def test_ai_adapt_unknown_preset_is_404() -> None:
    """不存在的预设返回 404。"""
    assert adapt(preset_id="nope").status_code == 404


def test_ai_adapt_apply_really_changes_preset() -> None:
    """端到端回灌：应用后重新载入，条目开关与顺序**真的**变了。

    只断言「接口返回了 patch」是不够的——补丁键名与覆盖层语义对不上时接口
    照样成功，但预设一点没变。这类缺陷只能靠回灌断言抓到。
    """
    preset = {
        "show_thoughts": True,
        "openai_max_tokens": 8192,
        "prompts": [
            {
                "identifier": "main",
                "name": "主提示",
                "role": "system",
                "content": "（测试语料）以角色身份回应。",
            },
            # 整条即模板 → 应被停用
            {
                "identifier": "template-rule",
                "name": "输出模板",
                "role": "system",
                "content": "（测试语料）<输出模板>按以下模板输出：<正文>…</正文>",
            },
            # 破限条目：内容保留，但要归拢到 jailbreak 槽位之后
            {
                "identifier": "jailbreak-extra",
                "name": "附加破限",
                "role": "system",
                "content": "（测试语料）本作允许破限题材。",
            },
            {
                "identifier": "jailbreak",
                "name": "破限槽位",
                "role": "system",
                "content": "（测试语料）保持角色口吻。",
            },
        ],
        "prompt_order": [
            {
                "character_id": 100000,
                "order": [
                    {"identifier": "main", "enabled": True},
                    {"identifier": "template-rule", "enabled": True},
                    {"identifier": "jailbreak-extra", "enabled": True},
                    {"identifier": "jailbreak", "enabled": True},
                ],
            }
        ],
    }
    client.post(
        IMPORT_URL, json={"content": preset, "source_file": "refill.json"}
    )

    assert adapt(preset_id="refill", dry_run=False).json()["applied"] is True

    detail = client.get("/chat/st-presets/refill").json()["detail"]
    assert detail["assembly"]["show_thoughts"] is False
    assert detail["sampling"]["max_tokens"] == 1024

    enabled = {entry["identifier"]: entry["enabled"] for entry in detail["order"]}
    assert enabled["template-rule"] is False        # 模板条目真的关上了
    assert enabled["jailbreak-extra"] is True       # 被保护的条目没被关

    positions = [entry["identifier"] for entry in detail["order"]]
    assert positions.index("jailbreak") + 1 == positions.index("jailbreak-extra")
