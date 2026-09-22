"""SqliteSessionRepository 测试。

核心保证是「刷新页面 / 重启后端都不丢」——因此**跨实例**的断言（新建一个
repository 指向同一文件，数据仍在）比单实例内的往返更重要。
"""

import pytest

from app.session.base import SessionStore
from app.session.context import ChatTurn
from app.session.repository import SessionRepository
from app.session.sqlite_repository import SqliteSessionRepository


@pytest.fixture
def store(tmp_path) -> SqliteSessionRepository:
    return SqliteSessionRepository(tmp_path / "sessions.db")


def test_create_and_get_roundtrip(store: SqliteSessionRepository) -> None:
    created = store.create("persona-a", user_name="小林", state_vars={"current_mood": "疲惫"})
    assert created.session_id
    assert created.created_at and created.updated_at

    fetched = store.get(created.session_id)
    assert fetched is not None
    assert fetched.persona_id == "persona-a"
    assert fetched.user_name == "小林"
    assert fetched.state_vars == {"current_mood": "疲惫"}
    assert fetched.history == []


def test_get_missing_returns_none(store: SqliteSessionRepository) -> None:
    assert store.get("不存在") is None


def test_duplicate_session_id_raises(store: SqliteSessionRepository) -> None:
    store.create("persona-a", session_id="fixed-id")
    with pytest.raises(ValueError, match="会话已存在"):
        store.create("persona-a", session_id="fixed-id")


def test_survives_new_instance(tmp_path) -> None:
    """核心保证：换一个实例（等价于重启后端）后，会话与消息都还在。"""
    db = tmp_path / "sessions.db"
    first = SqliteSessionRepository(db)
    created = first.create("persona-a")
    first.append_turn(created.session_id, ChatTurn(role="user", text="我叫小林"))
    first.append_turn(created.session_id, ChatTurn(role="assistant", text="记住了"))
    first.set_state_var(created.session_id, "current_mood", "平静")

    second = SqliteSessionRepository(db)  # 模拟进程重启
    restored = second.get(created.session_id)
    assert restored is not None
    assert restored.state_vars["current_mood"] == "平静"
    assert [t.text for t in restored.history] == ["我叫小林", "记住了"]
    assert restored.history[0].created_at  # 时间戳随消息落库


def test_history_window_trims_but_archive_is_kept(tmp_path) -> None:
    """窗口只影响喂给模型的条数，数据库里保留全部消息（界面历史因此完整）。"""
    store = SqliteSessionRepository(tmp_path / "sessions.db")
    session = store.create("persona-a", max_history_turns=4)
    for index in range(10):
        store.append_turn(session.session_id, ChatTurn(role="user", text=f"第{index}条"))

    # 默认（窗口）：最近 4 条
    assert [t.text for t in store.list_history(session.session_id)] == [
        "第6条",
        "第7条",
        "第8条",
        "第9条",
    ]
    # 显式放开上限：全部 10 条仍在
    assert len(store.list_history(session.session_id, limit=100)) == 10
    # get() 返回的也是窗口内的历史（模型上下文）
    fetched = store.get(session.session_id)
    assert fetched is not None
    assert len(fetched.history) == 4


def test_append_to_missing_session_raises(store: SqliteSessionRepository) -> None:
    with pytest.raises(KeyError, match="会话不存在"):
        store.append_turn("缺失", ChatTurn(role="user", text="在吗"))
    with pytest.raises(KeyError, match="会话不存在"):
        store.set_state_var("缺失", "current_mood", "平静")


def test_list_sessions_filters_sorts_and_summarizes(tmp_path) -> None:
    store = SqliteSessionRepository(tmp_path / "sessions.db")
    old = store.create("persona-a")
    store.append_turn(old.session_id, ChatTurn(role="user", text="最近总觉得睡不够，白天没精神"))
    store.append_turn(old.session_id, ChatTurn(role="assistant", text="先别急着怪自己"))
    other = store.create("persona-b")
    store.append_turn(other.session_id, ChatTurn(role="user", text="另一个角色的会话"))
    fresh = store.create("persona-a")  # 最新，且没有用户消息

    all_sessions = store.list_sessions()
    assert {item.session_id for item in all_sessions} == {
        old.session_id,
        other.session_id,
        fresh.session_id,
    }

    only_a = store.list_sessions(persona_id="persona-a")
    assert [item.session_id for item in only_a][0] == fresh.session_id  # 最近更新在前
    assert {item.session_id for item in only_a} == {old.session_id, fresh.session_id}

    target = next(item for item in only_a if item.session_id == old.session_id)
    assert target.message_count == 2
    assert target.title.startswith("最近总觉得睡不够")  # 标题取首条用户消息
    assert target.updated_at

    empty = next(item for item in only_a if item.session_id == fresh.session_id)
    assert empty.title == "新对话"
    assert empty.message_count == 0


def test_long_title_is_truncated(tmp_path) -> None:
    store = SqliteSessionRepository(tmp_path / "sessions.db")
    session = store.create("persona-a")
    long_text = "今天" * 40
    store.append_turn(session.session_id, ChatTurn(role="user", text=long_text))

    item = store.list_sessions(persona_id="persona-a")[0]
    assert item.title.endswith("…")
    assert len(item.title) <= 25  # 24 字 + 省略号


# ---------- 与内存实现的一致性 ----------


def test_both_implementations_share_contract(tmp_path) -> None:
    """两种存储满足同一接口与同一展示规则（标题 / 窗口 / 排序）。"""
    stores: list[SessionStore] = [
        SessionRepository(),
        SqliteSessionRepository(tmp_path / "sessions.db"),
    ]
    for store in stores:
        session = store.create("persona-a", max_history_turns=2)
        store.append_turn(session.session_id, ChatTurn(role="user", text="你好呀今天好累"))
        store.append_turn(session.session_id, ChatTurn(role="assistant", text="嗯，先歇会儿"))
        store.append_turn(session.session_id, ChatTurn(role="user", text="第三句"))

        assert len(store.list_history(session.session_id)) == 2  # 窗口
        assert len(store.list_history(session.session_id, limit=10)) == 3  # 留档
        summary = store.list_sessions(persona_id="persona-a")[0]
        assert summary.title == "你好呀今天好累"
        assert summary.message_count == 3
        assert store.get("缺失") is None


def test_non_positive_limit_returns_empty_in_both(tmp_path) -> None:
    """limit<=0 时两个实现都返回空。

    回归点：内存实现的 `archive[-0:]` 会返回**全部**消息（-0 == 0），
    与 SQLite 的 `LIMIT 0` 结果相反——同一配置下两种后端历史长度不一致。
    """
    stores: list[SessionStore] = [
        SessionRepository(),
        SqliteSessionRepository(tmp_path / "sessions.db"),
    ]
    for store in stores:
        session = store.create("persona-a")
        store.append_turn(session.session_id, ChatTurn(role="user", text="你好"))

        assert store.list_history(session.session_id, limit=0) == []
        assert store.list_history(session.session_id, limit=-1) == []
        assert len(store.list_history(session.session_id, limit=10)) == 1


def test_append_turns_writes_batch_atomically(tmp_path) -> None:
    """一轮对话的两条消息一次写入（避免只落一半留下孤立的用户消息）。"""
    store = SqliteSessionRepository(tmp_path / "sessions.db")
    session = store.create("persona-a")

    store.append_turns(
        session.session_id,
        [
            ChatTurn(role="user", text="你好"),
            ChatTurn(role="assistant", text="嗯，我在"),
        ],
    )

    assert [turn.role for turn in store.list_history(session.session_id)] == [
        "user",
        "assistant",
    ]
    assert store.list_sessions(persona_id="persona-a")[0].message_count == 2

    # 会话不存在 → 整批拒绝（不留下半条）
    with pytest.raises(KeyError, match="会话不存在"):
        store.append_turns("缺失", [ChatTurn(role="user", text="x")])


def test_delete_session_removes_session_and_messages(tmp_path) -> None:
    """删除会话：会话与消息一起清掉，返回删除条数（两种实现一致）。"""
    stores: list[SessionStore] = [
        SessionRepository(),
        SqliteSessionRepository(tmp_path / "sessions.db"),
    ]
    for store in stores:
        session = store.create("persona-a")
        store.append_turns(
            session.session_id,
            [
                ChatTurn(role="user", text="你好"),
                ChatTurn(role="assistant", text="嗯，我在"),
            ],
        )
        other = store.create("persona-a")
        store.append_turn(other.session_id, ChatTurn(role="user", text="另一段对话"))

        assert store.delete_session(session.session_id) == 2
        assert store.get(session.session_id) is None
        # 会话已不存在 → 读历史报错（而不是返回空列表或旧数据）
        with pytest.raises(KeyError, match="会话不存在"):
            store.list_history(session.session_id, limit=100)
        # 其它会话不受影响
        assert [item.session_id for item in store.list_sessions(persona_id="persona-a")] == [
            other.session_id
        ]

        # 不存在 → KeyError（路由层据此返回 404，而不是静默成功）
        with pytest.raises(KeyError, match="会话不存在"):
            store.delete_session(session.session_id)
