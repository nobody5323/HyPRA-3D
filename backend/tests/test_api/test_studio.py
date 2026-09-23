"""创作工坊接口测试（TestClient，无真实 LLM / embedding）。

覆盖四块：
1. 读取：catalog / 角色详情 / 条目详情（内置资源只读但可见）；
2. 角色与世界书的写入路径（含内置只读的 403 与参数错误的 400）；
3. 「试触发」逐通道结论；
4. **联动**：写完立刻在下一次对话里生效（验证对话图确实被失效重建）。
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BUILTIN_PERSONA = "therapist-elder-sister"
BUILTIN_ENTRY = "pet-cat"


# ---------- 读取 ----------


def test_catalog_lists_builtin_resources_and_hints() -> None:
    """catalog 一次带回角色 + 条目 + 写作提示。"""
    resp = client.get("/chat/studio/catalog")
    assert resp.status_code == 200
    body = resp.json()

    personas = {item["id"]: item for item in body["personas"]}
    assert personas[BUILTIN_PERSONA]["builtin"] is True
    # 清单刻意不带人设正文（详情接口才给）
    assert "prompt" not in personas[BUILTIN_PERSONA]

    entries = {item["id"]: item for item in body["entries"]}
    assert entries[BUILTIN_ENTRY]["builtin"] is True
    assert "猫" in entries[BUILTIN_ENTRY]["keys"]

    assert body["scope_all"] == "*"
    assert body["limits"]["persona_prompt"] > 0
    assert any(var["name"] == "user_name" for var in body["state_vars"])


def test_persona_detail_includes_body() -> None:
    resp = client.get(f"/chat/studio/personas/{BUILTIN_PERSONA}")
    assert resp.status_code == 200
    persona = resp.json()["persona"]
    assert persona["builtin"] is True
    assert persona["prompt"]
    assert persona["background"] == ""  # 内置角色没有背景故事


def test_entry_detail_includes_trigger_fields() -> None:
    resp = client.get(f"/chat/studio/worldbook/{BUILTIN_ENTRY}")
    assert resp.status_code == 200
    entry = resp.json()["entry"]
    assert entry["builtin"] is True
    assert entry["keys"] and entry["content"]
    assert entry["scope"] == "*"


def test_persona_and_entry_detail_404() -> None:
    assert client.get("/chat/studio/personas/user-nope").status_code == 404
    assert client.get("/chat/studio/worldbook/user-nope").status_code == 404


# ---------- 角色：写入 ----------


def test_create_persona_returns_catalog_and_detail() -> None:
    resp = client.post(
        "/chat/studio/personas",
        json={
            "name": "小岸",
            "title": "Seaside Friend",
            "prompt": "你是{{user_name}}的朋友小岸。",
            "background": "小岸在海边长大。",
            "tags": ["朋友"],
            "variables": ["user_name"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    persona_id = body["persona"]["id"]
    assert persona_id.startswith("user-")
    assert body["persona"]["background"] == "小岸在海边长大。"

    # 清单里立刻可见
    listed = {item["id"]: item for item in body["catalog"]["personas"]}
    assert listed[persona_id]["builtin"] is False


def test_create_persona_rejects_empty_fields() -> None:
    assert (
        client.post(
            "/chat/studio/personas", json={"name": "  ", "prompt": "正文"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/chat/studio/personas", json={"name": "名字", "prompt": ""}
        ).status_code
        == 400
    )


def test_create_persona_rejects_oversized_prompt() -> None:
    resp = client.post(
        "/chat/studio/personas", json={"name": "超长", "prompt": "字" * 8001}
    )
    assert resp.status_code == 400
    assert "人设正文" in resp.json()["detail"]


def test_update_persona_only_changes_given_fields() -> None:
    created = client.post(
        "/chat/studio/personas",
        json={"name": "小岸", "title": "Seaside", "prompt": "旧正文"},
    ).json()["persona"]

    resp = client.put(
        f"/chat/studio/personas/{created['id']}",
        json={"name": "小岸二号", "background": "换了背景。"},
    )
    assert resp.status_code == 200
    persona = resp.json()["persona"]
    assert persona["name"] == "小岸二号"
    assert persona["background"] == "换了背景。"
    assert persona["prompt"] == "旧正文"  # 未提交的字段不动
    assert persona["id"] == created["id"]  # id 是记忆命名空间，始终不变


def test_builtin_persona_is_read_only_over_http() -> None:
    assert (
        client.put(
            f"/chat/studio/personas/{BUILTIN_PERSONA}", json={"name": "改名"}
        ).status_code
        == 403
    )
    assert client.delete(f"/chat/studio/personas/{BUILTIN_PERSONA}").status_code == 403


def test_duplicate_builtin_persona_creates_editable_copy() -> None:
    resp = client.post(f"/chat/studio/personas/{BUILTIN_PERSONA}/duplicate", json={})
    assert resp.status_code == 200
    copy = resp.json()["persona"]
    assert copy["builtin"] is False
    assert copy["id"].startswith("user-")

    # 副本可改，原件不受影响
    assert (
        client.put(
            f"/chat/studio/personas/{copy['id']}", json={"name": "我的版本"}
        ).status_code
        == 200
    )
    assert (
        client.get(f"/chat/studio/personas/{BUILTIN_PERSONA}").json()["persona"]["name"]
        != "我的版本"
    )


def test_delete_persona_reports_impact() -> None:
    """删除角色只删角色卡，并在响应里说明留下了什么。"""
    persona = client.post(
        "/chat/studio/personas",
        json={"name": "小岸", "title": "Seaside", "prompt": "正文"},
    ).json()["persona"]

    # ① 该角色名下先有一段对话
    assert (
        client.post(
            "/chat", json={"text": "你好", "persona_id": persona["id"]}
        ).status_code
        == 200
    )
    # ② 再挂一条专属世界书条目
    client.post(
        "/chat/studio/worldbook",
        json={
            "title": "专属设定",
            "content": "只有小岸知道。",
            "keys": ["秘密"],
            "scope": persona["id"],
        },
    )

    resp = client.delete(f"/chat/studio/personas/{persona['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == persona["id"]
    assert body["sessions"] >= 1
    assert body["bound_entries"] == 1
    assert "保留" in body["kept"]

    # 角色卡没了，但会话与条目都还在
    assert client.get(f"/chat/studio/personas/{persona['id']}").status_code == 404
    assert body["sessions"] == len(
        client.get("/chat/sessions", params={"persona_id": persona["id"]}).json()
    )


# ---------- 世界书：写入 ----------


def test_create_entry_with_scope_and_triggers() -> None:
    persona = client.post(
        "/chat/studio/personas",
        json={"name": "小岸", "title": "Seaside", "prompt": "正文"},
    ).json()["persona"]

    resp = client.post(
        "/chat/studio/worldbook",
        json={
            "title": "小岸的猫",
            "content": "小岸养着一只叫「浪花」的白猫。",
            "keys": ["猫"],
            "regex": [r"浪\w*"],
            "vector_text": "用户提到养猫",
            "priority": 7,
            "scope": persona["id"],
        },
    )
    assert resp.status_code == 200
    entry = resp.json()["entry"]
    assert entry["scope"] == persona["id"]
    assert entry["priority"] == 7
    assert entry["builtin"] is False


def test_create_entry_rejects_invalid_input() -> None:
    # 没有触发条件
    assert (
        client.post(
            "/chat/studio/worldbook", json={"title": "空", "content": "正文"}
        ).status_code
        == 400
    )
    # 非法正则（必须在写入前拦住：matcher 是裸 re.search）
    resp = client.post(
        "/chat/studio/worldbook",
        json={"title": "坏正则", "content": "正文", "regex": ["(未闭合"]},
    )
    assert resp.status_code == 400
    assert "正则" in resp.json()["detail"]
    # 归属角色不存在
    assert (
        client.post(
            "/chat/studio/worldbook",
            json={"title": "幽灵", "content": "正文", "keys": ["x"], "scope": "nobody"},
        ).status_code
        == 400
    )


def test_update_and_delete_builtin_entry_are_rejected() -> None:
    assert (
        client.put(
            f"/chat/studio/worldbook/{BUILTIN_ENTRY}", json={"content": "改内容"}
        ).status_code
        == 403
    )
    assert client.delete(f"/chat/studio/worldbook/{BUILTIN_ENTRY}").status_code == 403
    assert (
        client.put("/chat/studio/worldbook/user-nope", json={"content": "x"}).status_code
        == 404
    )


def test_toggle_builtin_entry_never_touches_builtin_file() -> None:
    """内置条目的开关写在用户侧：接口表现为「已停用」，但内置对象没被改。"""
    off = client.patch(
        f"/chat/studio/worldbook/{BUILTIN_ENTRY}/enabled", json={"enabled": False}
    )
    assert off.status_code == 200
    assert off.json()["entry"]["enabled"] is False
    assert off.json()["entry"]["builtin"] is True

    catalog = {item["id"]: item for item in off.json()["catalog"]["entries"]}
    assert catalog[BUILTIN_ENTRY]["enabled"] is False

    on = client.patch(
        f"/chat/studio/worldbook/{BUILTIN_ENTRY}/enabled", json={"enabled": True}
    )
    assert on.json()["entry"]["enabled"] is True


def test_toggle_user_entry_updates_the_entry_itself() -> None:
    entry = client.post(
        "/chat/studio/worldbook",
        json={"title": "我的条目", "content": "正文", "keys": ["词"]},
    ).json()["entry"]

    resp = client.patch(
        f"/chat/studio/worldbook/{entry['id']}/enabled", json={"enabled": False}
    )
    assert resp.status_code == 200
    assert resp.json()["entry"]["enabled"] is False


def test_delete_user_entry() -> None:
    entry = client.post(
        "/chat/studio/worldbook",
        json={"title": "待删除", "content": "正文", "keys": ["词"]},
    ).json()["entry"]

    resp = client.delete(f"/chat/studio/worldbook/{entry['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == entry["id"]
    assert client.get(f"/chat/studio/worldbook/{entry['id']}").status_code == 404


# ---------- 试触发 ----------


def test_test_entry_reports_keyword_channel() -> None:
    resp = client.post(
        "/chat/studio/worldbook/test",
        json={
            "text": "我家的猫今天很黏人",
            "title": "我的设定",
            "content": "触发后注入的正文",
            "keys": ["猫", "茶"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True
    assert body["keys_hit"] == ["猫"]
    assert body["regex_hit"] == []
    assert "触发后注入的正文" in body["injected_text"]
    assert body["estimated_tokens"] > 0


def test_test_entry_reports_miss_reason() -> None:
    """没命中时也要给出各通道结论（否则用户不知道该怎么改）。"""
    body = client.post(
        "/chat/studio/worldbook/test",
        json={
            "text": "今天天气不错",
            "title": "我的设定",
            "content": "正文",
            "keys": ["猫"],
            "regex": [r"深夜|失眠"],
        },
    ).json()
    assert body["matched"] is False
    assert body["keys_hit"] == []
    assert body["regex_hit"] == []
    assert body["injected_text"] == ""


def test_test_entry_reports_regex_channel() -> None:
    body = client.post(
        "/chat/studio/worldbook/test",
        json={
            "text": "凌晨三点还醒着",
            "title": "深夜",
            "content": "正文",
            "regex": [r"凌晨|深夜"],
        },
    ).json()
    assert body["matched"] is True
    assert body["regex_hit"] == [r"凌晨|深夜"]


def test_test_entry_reports_vector_score() -> None:
    """声明了语义文本时返回相似度与阈值（本地确定性实现也能算）。"""
    body = client.post(
        "/chat/studio/worldbook/test",
        json={
            "text": "心里空落落的",
            "title": "孤独",
            "content": "正文",
            "vector_text": "用户感到孤独",
            "vector_threshold": 0.99,
        },
    ).json()
    assert body["matched"] is False  # 阈值 0.99 几乎不可能过
    assert isinstance(body["vector_score"], float)
    assert body["vector_threshold"] == 0.99


def test_test_entry_requires_trigger_and_valid_scope() -> None:
    resp = client.post(
        "/chat/studio/worldbook/test", json={"text": "随便说点什么", "content": "正文"}
    )
    assert resp.status_code == 400
    assert "触发条件" in resp.json()["detail"]

    assert (
        client.post(
            "/chat/studio/worldbook/test",
            json={"text": "随便说点什么", "keys": ["词"], "companion_id": "nobody"},
        ).status_code
        == 400
    )


# ---------- 联动：写完立刻在对话里生效 ----------


def test_created_entry_takes_effect_in_next_chat() -> None:
    """新建条目后**立刻**在下一次对话生效。

    回归点：对话图在构造时会快照世界书条目并一次性编码向量索引；写操作若不
    失效对话图，用户会看到「清单里有、对话里不生效」。
    """
    created = client.post(
        "/chat/studio/worldbook",
        json={
            "title": "梧桐叶邮局",
            "content": "苏澄会收集梧桐叶，寄给远方的朋友。",
            "keys": ["梧桐叶"],
        },
    ).json()["entry"]

    body = client.post("/chat", json={"text": "楼下的梧桐叶落了一地"}).json()

    assert created["id"] in body["worldbook_hits"]
    assert "梧桐叶邮局" in body["system_prompt"]
    assert "寄给远方的朋友" in body["system_prompt"]


def test_toggled_off_entry_stops_triggering_in_chat() -> None:
    """停用内置条目后，下一次对话不再注入它。"""
    before = client.post("/chat", json={"text": "我家猫今天很可爱"}).json()
    assert BUILTIN_ENTRY in before["worldbook_hits"]

    client.patch(
        f"/chat/studio/worldbook/{BUILTIN_ENTRY}/enabled", json={"enabled": False}
    )

    after = client.post("/chat", json={"text": "我家猫今天很可爱"}).json()
    assert BUILTIN_ENTRY not in after["worldbook_hits"]
    assert "团子" not in after["system_prompt"]
