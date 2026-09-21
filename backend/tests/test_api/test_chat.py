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
