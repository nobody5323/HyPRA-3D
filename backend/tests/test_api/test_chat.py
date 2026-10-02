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


def test_chat_reports_stage_timings() -> None:
    """★ 响应必须带分阶段耗时与 LLM 调用次数。

    「刚才那句为什么等了这么久」如果只能靠翻日志猜，优化就无从下手。
    这里锁住的是可观测性契约：召回各节点、图总耗时、路由总耗时都要在。
    """
    body = client.post("/chat", json={"text": "今天有点累"}).json()

    timings = body["timings"]
    for stage in (
        "load_persona",
        "worldbook_recall",
        "knowledge_recall",
        "memory_recall",
        "assemble_prompt",
        "generate_reply",
        "graph_total",
        "route_total",
    ):
        assert stage in timings, f"缺少阶段耗时：{stage}"
    assert timings["route_total"] >= 0
    assert body["llm_rounds"] >= 1


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


def test_purge_sessions_clears_only_that_persona() -> None:
    """按角色清空会话：目标角色清空，别的角色原样保留。

    「删多了」是这类批量操作最贵的错误（用户没地方找回来），所以两边都断言。
    """
    client.post("/chat", json={"text": "第一段对话"})
    client.post("/chat", json={"text": "第二段对话"})
    client.post("/chat", json={"text": "我是另一个角色", "persona_id": "energetic-roommate"})

    listed = client.get(
        "/chat/sessions", params={"persona_id": "therapist-elder-sister"}
    ).json()
    assert len(listed) == 2

    resp = client.delete("/chat/sessions", params={"persona_id": "therapist-elder-sister"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["removed_sessions"] == 2
    assert body["removed_turns"] == 4  # 每段一轮：user + assistant

    assert (
        client.get("/chat/sessions", params={"persona_id": "therapist-elder-sister"}).json()
        == []
    )
    others = client.get(
        "/chat/sessions", params={"persona_id": "energetic-roommate"}
    ).json()
    assert len(others) == 1


def test_purge_sessions_on_empty_persona_is_idempotent() -> None:
    """没有会话时返回 0，不报错——清空一个已经空的列表不该是错误。"""
    resp = client.delete("/chat/sessions", params={"persona_id": "energetic-roommate"})

    assert resp.status_code == 200
    assert resp.json()["removed_sessions"] == 0
    assert resp.json()["removed_turns"] == 0


def test_purge_memory_endpoint_is_idempotent() -> None:
    """清空记忆：四层键齐全，没数据时各层返回 0（幂等，不报错）。"""
    resp = client.delete("/chat/memory", params={"persona_id": "purge-probe"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["persona_id"] == "purge-probe"
    assert set(body["removed"]) == {"warm", "facts", "mood_log", "knowledge"}
    assert all(value == 0 for value in body["removed"].values())


def test_delete_session_missing_returns_404() -> None:
    resp = client.delete(
        "/chat/sessions/不存在", params={"persona_id": "therapist-elder-sister"}
    )
    assert resp.status_code == 404
    # persona_id 是必需参数：缺失即 422（防止误删其它角色的会话）
    assert client.delete("/chat/sessions/whatever").status_code == 422


def test_delete_session_rejects_other_persona() -> None:
    """不能删其它陪伴对象的会话（与 POST /chat 的同属校验一致）。

    第二个角色走创作工坊**真实创建**（而不是 monkeypatch 内部字典）：
    这样用例覆盖的就是用户自建角色后端的真实路径。
    """
    from app.api import chat as chat_module

    other = chat_module.get_studio_store().create_persona(
        name="第二个角色", title="Second Persona", prompt="你是另一个人。"
    )

    created = client.post(
        "/chat", json={"text": "你好", "persona_id": "therapist-elder-sister"}
    ).json()
    resp = client.delete(
        f"/chat/sessions/{created['session_id']}",
        params={"persona_id": other.id},
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


def test_chat_rejects_session_owned_by_other_persona() -> None:
    """会话必须属于同一个陪伴对象。

    否则会把 B 角色的轮次写进 A 角色的会话，破坏「会话按对象隔离」的契约。
    第二个角色走创作工坊真实创建，并失效对话图（与生产路径一致）。
    """
    from app.api import chat as chat_module

    other = chat_module.get_studio_store().create_persona(
        name="第二个角色", title="Second Persona", prompt="你是另一个人。"
    )
    chat_module.invalidate_chat_graph()

    created = client.post(
        "/chat", json={"text": "你好", "persona_id": "therapist-elder-sister"}
    )
    session_id = created.json()["session_id"]

    resp = client.post(
        "/chat",
        json={"text": "换个角色聊", "session_id": session_id, "persona_id": other.id},
    )
    assert resp.status_code == 404
    assert "不属于" in resp.json()["detail"]


def test_chat_with_user_created_persona() -> None:
    """用户自建角色：创建后立即可对话，人设 + 背景 + 专属世界书全部生效。

    这条用例覆盖创作工坊接入对话链路的整条路径：存储写入 → 对话图重建 →
    人设渲染（含背景故事块）→ 世界书按归属过滤。
    """
    from app.api import chat as chat_module

    store = chat_module.get_studio_store()
    persona = store.create_persona(
        name="小岸",
        title="Seaside Friend",
        prompt="你是{{user_name}}的朋友小岸，说话简短。",
        background="小岸在海边长大，听得懂潮水的声音。",
    )
    store.create_entry(
        title="小岸的猫",
        content="小岸养着一只叫「浪花」的白猫。",
        keys=["猫"],
        scope=persona.id,
    )
    chat_module.invalidate_chat_graph()

    # ① 立刻出现在人设清单里，并标记为「我的」
    catalog = client.get("/chat/personas").json()
    mine = [item for item in catalog["personas"] if item["id"] == persona.id]
    assert mine and mine[0]["builtin"] is False

    # ② 直接对话：人设正文与背景故事都进入系统提示
    resp = client.post(
        "/chat",
        json={"text": "你家的猫怎么样", "persona_id": persona.id, "user_name": "小林"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "小岸" in body["system_prompt"]
    assert "小岸在海边长大，听得懂潮水的声音。" in body["system_prompt"]
    assert "[背景故事]" in body["system_prompt"]
    # ③ 专属世界书条目命中（「浪花」只在专属条目里出现）
    assert "浪花" in body["system_prompt"]

    # ④ 同一句话在**另一个**角色下不注入专属设定（归属隔离）
    other = client.post(
        "/chat", json={"text": "你家的猫怎么样", "persona_id": "therapist-elder-sister"}
    )
    assert other.status_code == 200
    assert "浪花" not in other.json()["system_prompt"]
    assert "[背景故事]" not in other.json()["system_prompt"]


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


# --------------------------------------------------------------------------
# 感知层进提示词（§4.5 / §4.8 / §4.9）
# --------------------------------------------------------------------------


def test_chat_prompt_always_carries_the_current_time() -> None:
    """时间**不依赖桌宠端上报**：只要后端在跑，模型就该知道现在是什么时候。

    这条是 §4.8 的核心：原先时间只存在于桌面情景上报里，
    于是 Web 端永远没有时间——模型会在凌晨两点说「今天过得怎么样呀」。
    """
    body = client.post("/chat", json={"text": "在吗"}).json()
    prompt = body["system_prompt"]

    assert "[此刻]" in prompt
    assert "现在是 " in prompt


def test_chat_prompt_carries_recent_activity_after_a_desktop_report() -> None:
    """行踪与画像真的进了模型看到的提示词（不只是接口里能查到）。"""
    client.post(
        "/perception/desktop",
        json={
            "foreground_process": "Code.exe",
            "foreground_title": "",
            "idle_seconds": 1.0,
            "local_time": "22:30",
            "local_date": "2026-09-29",
        },
    )
    body = client.post("/chat", json={"text": "在吗"}).json()
    prompt = body["system_prompt"]

    assert "[此刻]" in prompt
    assert "Code.exe" in prompt


def test_chat_prompt_omits_activity_when_disabled(monkeypatch) -> None:
    """关掉行踪后提示词里不该出现任何程序名（关就是真的关）。"""
    monkeypatch.setenv("PERCEPTION_ACTIVITY_ENABLED", "false")
    client.post(
        "/perception/desktop",
        json={"foreground_process": "Code.exe", "idle_seconds": 1.0},
    )
    body = client.post("/chat", json={"text": "在吗"}).json()
    assert "Code.exe" not in body["system_prompt"]
