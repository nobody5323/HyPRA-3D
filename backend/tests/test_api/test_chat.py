"""POST /chat API 测试（TestClient，无真实 LLM）。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_chat_returns_assembled_prompt() -> None:
    resp = client.post("/chat", json={"text": "我失眠了"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"]
    assert body["system_prompt"].startswith("[角色人设]")
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][-1]["role"] == "user"
    # 触发世界书：失眠 → 深夜模式
    assert "night-mode" in body["worldbook_hits"]
    assert body["note"]


def test_chat_continuation_keeps_history() -> None:
    """同一 session_id 续聊：上一轮输入进入 messages 历史。"""
    first = client.post("/chat", json={"text": "昨天被老板批评了", "user_name": "小林"})
    sid = first.json()["session_id"]

    second = client.post("/chat", json={"text": "今天还是难受", "session_id": sid})
    assert second.status_code == 200
    roles = [m["role"] for m in second.json()["messages"]]
    contents = [m["content"] for m in second.json()["messages"]]
    # 历史含第一轮 user 输入
    assert "user" in roles[1:-1]
    assert "昨天被老板批评了" in contents
    assert contents[-1] == "今天还是难受"


def test_chat_schedules_memory_write_in_background() -> None:
    """记忆写入提交后台：响应只标记「已调度」，不返回写入统计。

    生产环境下 Starlette 的 Response.__call__ 是
    `send(response) → await background()`，即响应字节先发出、后台任务后执行，
    因此抽取（LLM 版需 1–3 秒）不会阻塞用户拿到回复。
    """
    resp = client.post("/chat", json={"text": "我叫小林，喜欢下雨天"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["memory_scheduled"] is True
    assert "remembered" not in body          # 旧字段已移除
    # 个人记忆召回单独计数（本轮未上传任何资料，应为 0）
    assert body["knowledge_hits"] == 0


def test_chat_memory_written_by_background_job() -> None:
    """后台写入确实生效：续聊时上一轮内容能被召回进 prompt。

    TestClient 会等待 background tasks 完成，所以可直接断言结果。
    """
    first = client.post("/chat", json={"text": "我最怕打雷，会躲进被子"})
    sid = first.json()["session_id"]

    second = client.post("/chat", json={"text": "今天又打雷了", "session_id": sid})
    body = second.json()

    assert body["memory_counts"]["memories"] >= 1
    assert "打雷" in body["system_prompt"]


def test_chat_unknown_persona_404() -> None:
    resp = client.post("/chat", json={"text": "hi", "persona_id": "no-such-persona"})
    assert resp.status_code == 404


def test_chat_empty_text_422() -> None:
    resp = client.post("/chat", json={"text": ""})
    assert resp.status_code == 422


# ---------- 模型预设（选择器接口）----------


def test_presets_endpoint_lists_profiles() -> None:
    """GET /chat/presets：界面选择器的数据源。"""
    resp = client.get("/chat/presets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"]
    assert body["auto_preset_id"]
    ids = [item["id"] for item in body["presets"]]
    assert "default" in ids
    assert all(item["label"] for item in body["presets"])  # 展示名不能为空


def test_personas_endpoint_lists_presets() -> None:
    """GET /chat/personas：界面「陪伴对象」选择器 + 页面角色名的数据源。"""
    resp = client.get("/chat/personas")
    assert resp.status_code == 200
    body = resp.json()
    ids = [item["id"] for item in body["personas"]]
    # 缺省人设必须在清单垦里（否则前端切回去只能靠硬编码）
    assert body["default_persona_id"] in ids
    assert all(item["name"] and item["title"] for item in body["personas"])


def test_styles_endpoint_lists_presets() -> None:
    """GET /chat/styles：界面「文风」选择器的数据源。"""
    resp = client.get("/chat/styles")
    assert resp.status_code == 200
    body = resp.json()
    ids = [item["id"] for item in body["styles"]]
    assert body["default_style_id"] in ids
    assert all(item["name"] and item["description"] for item in body["styles"])


def test_chat_accepts_explicit_preset_id() -> None:
    """显式 preset_id 生效，并把本轮实际生效项回传到响应。"""
    resp = client.post("/chat", json={"text": "聊聊吧", "preset_id": "default"})
    assert resp.status_code == 200
    preset = resp.json()["preset"]
    assert preset["preset_id"] == "default"
    assert preset["preset_label"]
    assert "temperature" in preset


def test_chat_includes_memory_recall(tmp_path) -> None:
    """预置记忆后，召回内容应进入 system_prompt 的记忆块。"""
    from app.api import chat as chat_module
    from app.memory.cold.sqlite_store import SqliteColdStore
    from app.memory.store import MemoryStore
    from app.memory.warm.inmemory_store import InMemoryWarmStore

    cold = SqliteColdStore(db_path=tmp_path / "chat_memory.db")
    warm = InMemoryWarmStore()
    warm.add("therapist-elder-sister", "小林说过他最怕打雷，会躲进被子里")
    chat_module.set_memory_store(MemoryStore(cold, warm))

    resp = client.post("/chat", json={"text": "今天又打雷了"})
    body = resp.json()
    assert resp.status_code == 200
    assert "[记忆回忆]" in body["system_prompt"]
    assert "怕打雷" in body["system_prompt"]
    assert body["memory_counts"]["memories"] >= 1


def test_chat_memory_block_after_worldbook() -> None:
    """记忆块应排在世界书节之后（固定顺序）。"""
    from app.api import chat as chat_module
    from app.memory.cold.sqlite_store import SqliteColdStore
    from app.memory.store import MemoryStore
    from app.memory.warm.inmemory_store import InMemoryWarmStore
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp()) / "m.db"
    warm = InMemoryWarmStore()
    warm.add("therapist-elder-sister", "小林睡不着时喜欢聊猫")
    chat_module.set_memory_store(MemoryStore(SqliteColdStore(db_path=tmp), warm))

    # 该输入同时命中世界书（失眠→night-mode）与记忆（失眠/猫）
    body = client.post("/chat", json={"text": "我又失眠了，想起上次聊的猫"}).json()
    prompt = body["system_prompt"]
    assert "[场景补充]" in prompt and "[记忆回忆]" in prompt
    assert prompt.index("[场景补充]") < prompt.index("[记忆回忆]")


# ---------- 会话历史（刷新页面 / 重启后端后的恢复）----------


def test_session_history_restores_messages() -> None:
    """历史接口返回原始留档，供界面刷新后恢复对话。"""
    created = client.post("/chat", json={"text": "我叫小林，喜欢下雨天"})
    session_id = created.json()["session_id"]

    resp = client.get(f"/chat/sessions/{session_id}/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["persona_id"] == "therapist-elder-sister"
    assert body["user_name"]
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["text"] == "我叫小林，喜欢下雨天"
    assert body["messages"][0]["created_at"]  # 时间戳随消息落库


def test_session_list_summarizes_conversations() -> None:
    """会话列表带标题 / 消息数 / 更新时间，支持按陪伴对象过滤。"""
    client.post("/chat", json={"text": "最近总是睡不好，白天没精神"})

    resp = client.get(
        "/chat/sessions", params={"persona_id": "therapist-elder-sister"}
    )
    assert resp.status_code == 200
    items = resp.json()
    assert items
    assert items[0]["message_count"] >= 2
    assert items[0]["title"].startswith("最近总是睡不好")
    assert items[0]["updated_at"]


def test_session_history_missing_returns_404() -> None:
    assert client.get("/chat/sessions/不存在/history").status_code == 404


def test_delete_session_endpoint() -> None:
    """删除对话：该会话消失、历史变 404，其它会话不受影响。"""
    first = client.post("/chat", json={"text": "第一段对话"}).json()
    client.post("/chat", json={"text": "第二段对话"})
    listed = client.get(
        "/chat/sessions", params={"persona_id": "therapist-elder-sister"}
    ).json()
    assert len(listed) == 2

    resp = client.delete(
        f"/chat/sessions/{first['session_id']}",
        params={"persona_id": "therapist-elder-sister"},
    )
    assert resp.status_code == 200
    assert resp.json()["removed_turns"] == 2
    assert client.get(f"/chat/sessions/{first['session_id']}/history").status_code == 404
    remaining = client.get(
        "/chat/sessions", params={"persona_id": "therapist-elder-sister"}
    ).json()
    assert len(remaining) == 1


def test_delete_session_missing_returns_404() -> None:
    resp = client.delete(
        "/chat/sessions/不存在", params={"persona_id": "therapist-elder-sister"}
    )
    assert resp.status_code == 404
    # persona_id 是必需参数：缺失即 422（防止误删其它角色的会话）
    assert client.delete("/chat/sessions/whatever").status_code == 422


def test_delete_session_rejects_other_persona(monkeypatch) -> None:
    """不能删其它陪伴对象的会话（与 POST /chat 的同属校验一致）。"""
    from app.api import chat as chat_module

    persona = chat_module._presets["therapist-elder-sister"]
    monkeypatch.setitem(
        chat_module._presets,
        "second-persona",
        persona.model_copy(update={"id": "second-persona"}),
    )

    created = client.post(
        "/chat", json={"text": "你好", "persona_id": "therapist-elder-sister"}
    ).json()
    resp = client.delete(
        f"/chat/sessions/{created['session_id']}",
        params={"persona_id": "second-persona"},
    )
    assert resp.status_code == 404
    assert "不属于" in resp.json()["detail"]
    # 确认真的没被删
    assert client.get(f"/chat/sessions/{created['session_id']}/history").status_code == 200


def test_chat_continues_session_after_restore() -> None:
    """拿到会话 id（刷新页面后从本地存储取回）继续聊，模型仍看得到上一轮。"""
    created = client.post("/chat", json={"text": "我最怕打雷，会躲进被子"})
    session_id = created.json()["session_id"]

    second = client.post(
        "/chat", json={"text": "今天又打雷了", "session_id": session_id}
    )
    assert second.status_code == 200
    contents = [message["content"] for message in second.json()["messages"]]
    assert "我最怕打雷，会躲进被子" in contents


def test_chat_applies_current_mood_to_existing_session() -> None:
    """续聊时传 current_mood 必须**立即**生效。

    回归点：SQLite 实现的 set_state_var 返回**新对象**，而会话快照是旧的；
    调用方若不接住返回值，本轮 {{current_mood}} 会退化成默认值并多出一条告警
    （内存实现是就地修改，所以这个 bug 只在默认的 sqlite 后端下出现）。
    """
    created = client.post("/chat", json={"text": "随便聊聊"})
    session_id = created.json()["session_id"]

    resp = client.post(
        "/chat",
        json={"text": "今天很烦", "session_id": session_id, "current_mood": "烦躁"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "烦躁" in body["system_prompt"]
    assert not any("current_mood" in warning for warning in body["warnings"])


def test_chat_rejects_session_owned_by_other_persona(monkeypatch) -> None:
    """会话必须属于同一个陪伴对象。

    否则会把 B 角色的轮次写进 A 角色的会话，破坏「会话按对象隔离」的契约。
    当前只有一个内置人设，这里临时注册第二个来构造该组合。
    """
    from app.api import chat as chat_module

    persona = chat_module._presets["therapist-elder-sister"]
    monkeypatch.setitem(
        chat_module._presets,
        "second-persona",
        persona.model_copy(update={"id": "second-persona"}),
    )

    created = client.post(
        "/chat", json={"text": "你好", "persona_id": "therapist-elder-sister"}
    )
    session_id = created.json()["session_id"]

    resp = client.post(
        "/chat",
        json={"text": "换个角色聊", "session_id": session_id, "persona_id": "second-persona"},
    )
    assert resp.status_code == 404
    assert "不属于" in resp.json()["detail"]


def test_chat_reports_knowledge_hits() -> None:
    """个人记忆（知识库）召回行数在响应中单独暴露。

    前端据此展示「这次用上了你上传的资料」——memory_counts 只含情景记忆与
    语义事实两层，个人记忆需要独立出口（见 ChatResponse.knowledge_hits）。
    """
    text = "我最喜欢的季节是深秋，因为梧桐叶会铺满整条街。" * 12
    upload = client.post(
        "/knowledge/upload",
        params={"companion_id": "therapist-elder-sister"},
        data={"text": text},
    )
    assert upload.status_code == 200

    body = client.post("/chat", json={"text": "深秋的梧桐叶铺满整条街"}).json()
    assert body["knowledge_hits"] >= 1
    assert "[参考资料]" in body["system_prompt"]
