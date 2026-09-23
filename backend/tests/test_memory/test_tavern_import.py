"""酒馆会话导入器测试：轮次配对、幂等、容错、作用域隔离。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.tavern_import import TavernMemoryImporter, pair_turns
from app.memory.warm.inmemory_store import InMemoryWarmStore
from app.plugins.contracts import DataSourceMessage, DataSourceSession


def _msg(role: str, text: str) -> DataSourceMessage:
    return DataSourceMessage(role=role, text=text)


def _session(session_id: str, *messages: tuple[str, str], character: str = "示例角色") -> DataSourceSession:
    return DataSourceSession(
        id=session_id,
        character_name=character,
        title=f"会话 {session_id}",
        messages=tuple(_msg(role, text) for role, text in messages),
    )


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(
        warm=InMemoryWarmStore(),
        cold=SqliteColdStore(db_path=tmp_path / "memory.db"),
    )


@pytest.fixture
def importer(store: MemoryStore, tmp_path: Path) -> TavernMemoryImporter:
    return TavernMemoryImporter(store, state_path=tmp_path / "tavern_import.json")


# =============================================================
# 轮次配对
# =============================================================


def test_pair_turns_basic_alternation() -> None:
    turns = pair_turns(
        [_msg("user", "你好"), _msg("assistant", "你好呀"), _msg("user", "今天很累"),
         _msg("assistant", "辛苦了")]
    )
    assert turns == [("你好", "你好呀"), ("今天很累", "辛苦了")]


def test_pair_turns_merges_consecutive_user_messages() -> None:
    """连续多条 user（酒馆里很常见）拼成一句，不丢内容。"""
    turns = pair_turns(
        [_msg("user", "在吗"), _msg("user", "我想说个事"), _msg("assistant", "在的")]
    )
    assert turns == [("在吗 我想说个事", "在的")]


def test_pair_turns_handles_leading_assistant() -> None:
    """以 assistant 开头（如开场白）：与空 user 配对，不丢这段内容。"""
    turns = pair_turns([_msg("assistant", "今天过得怎么样？"), _msg("user", "还行")])
    assert turns == [("", "今天过得怎么样？"), ("还行", "")]


def test_pair_turns_ignores_system_and_empty() -> None:
    turns = pair_turns(
        [_msg("system", "系统提示"), _msg("user", "  "), _msg("user", "甲"), _msg("assistant", "乙")]
    )
    assert turns == [("甲", "乙")]


def test_pair_turns_empty_input() -> None:
    assert pair_turns([]) == []


# =============================================================
# 导入
# =============================================================


def test_import_writes_memory_and_facts(importer, store) -> None:
    """导入后能召回——这是「跨会话长期记忆」的最小闭环证据。"""
    sessions = [
        _session("s1", ("user", "我住在杭州"), ("assistant", "杭州很美")),
        _session("s2", ("user", "我养了一只猫叫豆豆"), ("assistant", "豆豆真可爱")),
    ]

    result = importer.import_sessions(sessions, companion_id="comp-a")

    assert result.sessions_total == 2
    assert result.sessions_imported == 2
    assert result.turns == 2
    assert result.sessions_skipped == 0
    # 记忆库里确实有东西了（情景或事实至少写进去一份）
    assert result.memories + result.facts > 0
    assert result.warnings == []


def test_import_is_idempotent(importer) -> None:
    """重复导入同一批会话：第二次全部跳过（不重复污染记忆）。"""
    sessions = [_session("s1", ("user", "我喜欢下雨天"), ("assistant", "嗯"))]

    first = importer.import_sessions(sessions, companion_id="comp-a")
    second = importer.import_sessions(sessions, companion_id="comp-a")

    assert first.sessions_imported == 1
    assert second.sessions_imported == 0
    assert second.sessions_skipped == 1
    assert second.turns == 0
    assert importer.imported_sessions("comp-a") == ["s1"]


def test_force_reimport(importer) -> None:
    """force=True 忽略记录重新导入（修复数据 / 换 embedding 后重建用）。"""
    sessions = [_session("s1", ("user", "甲"), ("assistant", "乙"))]
    importer.import_sessions(sessions, companion_id="comp-a")

    forced = importer.import_sessions(sessions, companion_id="comp-a", force=True)

    assert forced.sessions_imported == 1
    assert forced.turns == 1


def test_import_isolated_by_companion(importer, store) -> None:
    """记忆按陪伴对象隔离（§8.2）：导入 A 不该让 B 召回得到。"""
    importer.import_sessions(
        [_session("s1", ("user", "我的猫叫豆豆"), ("assistant", "记住啦"))],
        companion_id="comp-a",
    )

    assert store.recall("comp-b", "猫叫什么").empty is True
    assert importer.imported_sessions("comp-b") == []


def test_sessions_tracked_per_companion(importer) -> None:
    """导入记录按陪伴对象分开维护。"""
    importer.import_sessions([_session("s1", ("user", "甲"), ("assistant", "乙"))],
                            companion_id="comp-a")
    importer.import_sessions([_session("s1", ("user", "丙"), ("assistant", "丁"))],
                            companion_id="comp-b")

    assert importer.imported_sessions("comp-a") == ["s1"]
    assert importer.imported_sessions("comp-b") == ["s1"]


def test_empty_session_skipped_with_warning(importer) -> None:
    result = importer.import_sessions([_session("s1")], companion_id="comp-a")

    assert result.sessions_imported == 0
    assert result.sessions_skipped == 1
    assert any("没有可用轮次" in w for w in result.warnings)
    # 空会话不应被记为"已导入"（否则将来补了内容也不会再导入）
    assert importer.imported_sessions("comp-a") == []


def test_write_failure_recorded_not_raised(importer, monkeypatch) -> None:
    """单轮写入失败只记 warning，其余轮次照常导入。"""
    calls: list[int] = []
    original = importer.memory_store.remember_turn

    def flaky(companion_id, user_text, assistant_text, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("模拟向量库不可用")
        return original(companion_id, user_text, assistant_text, **kwargs)

    monkeypatch.setattr(importer.memory_store, "remember_turn", flaky)

    result = importer.import_sessions(
        [_session("s1", ("user", "甲"), ("assistant", "乙"), ("user", "丙"), ("assistant", "丁"))],
        companion_id="comp-a",
    )

    assert result.turns == 1                     # 第二轮成功
    assert any("模拟向量库不可用" in w for w in result.warnings)
    assert result.sessions_imported == 1          # 会话整体仍算导入


def test_requires_companion_id(importer) -> None:
    """记忆作用域不可为空——否则会写进一个无归属的命名空间。"""
    with pytest.raises(ValueError, match="companion_id"):
        importer.import_sessions([], companion_id="")


def test_reset_clears_record_but_keeps_memory(importer, store) -> None:
    sessions = [_session("s1", ("user", "我喜欢喝茶"), ("assistant", "嗯"))]
    importer.import_sessions(sessions, companion_id="comp-a")

    importer.reset("comp-a")

    assert importer.imported_sessions("comp-a") == []
    # 记忆本体仍在（reset 只清"已导入"记录）
    again = importer.import_sessions(sessions, companion_id="comp-a")
    assert again.sessions_imported == 1


def test_corrupt_state_file_treated_as_empty(importer, tmp_path) -> None:
    importer.state_path.write_text("{不是 JSON", encoding="utf-8")

    assert importer.imported_sessions("comp-a") == []
    result = importer.import_sessions([_session("s1", ("user", "甲"), ("assistant", "乙"))],
                                      companion_id="comp-a")
    assert result.sessions_imported == 1


def test_state_file_persists_across_instances(store, tmp_path) -> None:
    """导入记录落盘——重启后不会重复导入。"""
    state_path = tmp_path / "tavern_import.json"
    first = TavernMemoryImporter(store, state_path=state_path)
    first.import_sessions([_session("s1", ("user", "甲"), ("assistant", "乙"))],
                          companion_id="comp-a")

    fresh = TavernMemoryImporter(store, state_path=state_path)
    assert fresh.imported_sessions("comp-a") == ["s1"]
    assert fresh.import_sessions([_session("s1", ("user", "甲"), ("assistant", "乙"))],
                                 companion_id="comp-a").sessions_skipped == 1


def test_result_as_dict_shape() -> None:
    from app.memory.tavern_import import ImportResult

    payload = ImportResult(sessions_total=2, sessions_imported=1, warnings=["甲"]).as_dict()
    assert payload["sessions_total"] == 2
    assert payload["warnings"] == ["甲"]
    assert set(payload) == {
        "sessions_total", "sessions_imported", "sessions_skipped",
        "turns", "facts", "memories", "warnings",
    }
