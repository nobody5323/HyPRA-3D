"""酒馆会话导入器测试：轮次配对、**增量游标**、容错、作用域隔离。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.memory.cold.sqlite_store import SqliteColdStore
from app.memory.store import MemoryStore
from app.memory.tavern_import import (
    CURSOR_UNKNOWN,
    TavernMemoryImporter,
    completed_turns,
    pair_turns,
)
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


@pytest.fixture
def recorded(importer: TavernMemoryImporter, monkeypatch) -> list[tuple[str, str]]:
    """记录每一次 `remember_turn` 的 `(用户, 助手)`，用于断言「到底写了哪几轮」。

    比看轮次计数强：增量导入的关键承诺是「不重复写」，
    要看的是**写了什么**，而不只是写了几轮。
    """
    seen: list[tuple[str, str]] = []
    original = importer.memory_store.remember_turn

    def spy(companion_id, user_text, assistant_text, **kwargs):
        seen.append((user_text, assistant_text))
        return original(companion_id, user_text, assistant_text, **kwargs)

    monkeypatch.setattr(importer.memory_store, "remember_turn", spy)
    return seen


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
# 已完成轮次（增量游标的基准）
# =============================================================


def test_completed_turns_drops_the_pending_user_turn() -> None:
    """结尾悬空的用户消息不计入「已完成轮次」（它还没有助手回复）。"""
    turns = completed_turns(
        [_msg("user", "甲"), _msg("assistant", "乙"), _msg("user", "丙")]
    )

    assert turns == [("甲", "乙")]


def test_completed_turns_keeps_a_leading_assistant_turn() -> None:
    """开场白式轮次（`("", 助手)`）助手有正文，算完成——它不能被当成悬空轮丢掉。"""
    turns = completed_turns([_msg("assistant", "开场白"), _msg("user", "甲")])

    assert turns == [("", "开场白")]


def test_completed_turns_all_finished() -> None:
    turns = completed_turns([_msg("user", "甲"), _msg("assistant", "乙")])
    assert turns == [("甲", "乙")]


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
    assert any("没有可导入的完整轮次" in w for w in result.warnings)
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

    payload = ImportResult(
        sessions_total=2, sessions_imported=1, scopes={"tbp-a": 3}, warnings=["甲"]
    ).as_dict()
    assert payload["sessions_total"] == 2
    assert payload["scopes"] == {"tbp-a": 3}
    assert payload["warnings"] == ["甲"]
    assert set(payload) == {
        "sessions_total", "sessions_imported", "sessions_skipped",
        "turns", "facts", "memories", "scopes", "warnings",
    }


# =============================================================
# 增量游标：酒馆里不断新增的对话怎么被记住
# =============================================================


def _grown(*pairs: tuple[str, str], session_id: str = "s1") -> DataSourceSession:
    """把若干轮次展成会话的消息序列（酒馆 jsonl 就是一直往后追加的）。"""
    messages: list[tuple[str, str]] = []
    for user_text, assistant_text in pairs:
        if user_text:
            messages.append(("user", user_text))
        if assistant_text:
            messages.append(("assistant", assistant_text))
    return _session(session_id, *messages)


def test_incremental_import_picks_up_new_turns(importer, recorded) -> None:
    """★ 同一段剧情接着聊：再导入只写新增的轮次，已导过的绝不重写。

    旧实现按**会话**幂等（`if session.id in imported: skip`），而酒馆是在同一个
    jsonl 文件里往后追加的——结果是「继续聊的内容永远进不来」，
    唯一的绕过是 force 全量重导，代价是重复写入。
    """
    importer.import_sessions(
        [_grown(("甲", "乙"))], companion_id="comp-a"
    )
    assert recorded == [("甲", "乙")]

    grown = importer.import_sessions(
        [_grown(("甲", "乙"), ("丙", "丁"), ("戊", "己"))], companion_id="comp-a"
    )

    assert recorded == [("甲", "乙"), ("丙", "丁"), ("戊", "己")]
    assert grown.turns == 2                       # 只写了新增的两轮
    assert grown.sessions_imported == 1
    assert importer.imported_turns("comp-a", "s1") == 3


def test_reimport_without_new_turns_writes_nothing(importer, recorded) -> None:
    """没新内容时重复点也不写（幂等是增量的特例）。"""
    sessions = [_grown(("甲", "乙"))]
    importer.import_sessions(sessions, companion_id="comp-a")
    recorded.clear()

    again = importer.import_sessions(sessions, companion_id="comp-a")

    assert again.turns == 0
    assert again.sessions_skipped == 1
    assert recorded == []


def test_new_session_imports_alongside_an_old_one(importer, recorded) -> None:
    """新会话能进，同时老会话里新增的轮次也能进——两者互不干扰。"""
    importer.import_sessions([_grown(("甲", "乙"))], companion_id="comp-a")
    recorded.clear()

    result = importer.import_sessions(
        [_grown(("甲", "乙"), ("丙", "丁")), _grown(("新", "会话"), session_id="s2")],
        companion_id="comp-a",
    )

    assert sorted(recorded) == [("丙", "丁"), ("新", "会话")]
    assert result.turns == 2
    assert importer.imported_sessions("comp-a") == ["s1", "s2"]


def test_pending_user_turn_waits_for_the_reply(importer, recorded) -> None:
    """★ 结尾悬空的用户消息不导入也不推进游标，等回复到了再一起进。

    否则游标会把它算作「导过」；等助手回复来了这一轮的配对变成
    `(用户那句, 助手回复)`，助手那句就**永久丢失**——这是增量导入最容易踩的坑。
    """
    importer.import_sessions([_grown(("甲", "乙"))], companion_id="comp-a")
    recorded.clear()

    # 用户在酒馆里又发了一句，助手还没回
    importer.import_sessions([_grown(("甲", "乙"), ("丙", ""))], companion_id="comp-a")

    assert recorded == []                                    # 悬空那句没写
    assert importer.imported_turns("comp-a", "s1") == 1      # 游标没推进

    # 助手回复到了 → 这一轮（用户那句 + 助手回复）一起进
    importer.import_sessions([_grown(("甲", "乙"), ("丙", "丁"))], companion_id="comp-a")

    assert recorded == [("丙", "丁")]
    assert importer.imported_turns("comp-a", "s1") == 2


def test_force_restarts_from_zero(importer, recorded) -> None:
    """force 从**头**重导（它会重复写入，界面必须二次确认）。"""
    sessions = [_grown(("甲", "乙"), ("丙", "丁"))]
    importer.import_sessions(sessions, companion_id="comp-a")
    recorded.clear()

    forced = importer.import_sessions(sessions, companion_id="comp-a", force=True)

    assert forced.turns == 2
    assert recorded == [("甲", "乙"), ("丙", "丁")]


def test_imported_turns_reports_progress(importer) -> None:
    assert importer.imported_turns("comp-a", "s1") == 0      # 没导过

    importer.import_sessions([_grown(("甲", "乙"))], companion_id="comp-a")

    assert importer.imported_turns("comp-a", "s1") == 1
    assert importer.imported_turns("comp-a", "别的会话") == 0


def test_shorter_session_is_clamped_with_warning(importer, recorded) -> None:
    """会话被编辑/删短：钳到当前长度并告警，**不倒退重导**（重导会写重复项）。"""
    importer.import_sessions([_grown(("甲", "乙"), ("丙", "丁"))], companion_id="comp-a")
    recorded.clear()

    result = importer.import_sessions([_grown(("甲", "乙"))], companion_id="comp-a")

    assert result.turns == 0
    assert recorded == []
    assert any("更短" in w for w in result.warnings)
    assert importer.imported_turns("comp-a", "s1") == 1


# =============================================================
# 旧格式（只有会话粒度）的迁移
# =============================================================


def test_legacy_state_migrates_to_a_baseline(importer, recorded) -> None:
    """★ 旧记录（`{companion: [会话id]}`）迁移时不重复导入，之后增量生效。

    旧格式不知道导到第几轮。两边的代价不对称：重复导入会往向量库与事实表
    塞重复项（召回时占位），而旧格式下新增的轮次本来就进不来——
    所以取「不重复」那边，按当前轮数建基线，并明确告诉用户怎么补。
    """
    importer.state_path.write_text(
        json.dumps({"comp-a": ["s1"]}, ensure_ascii=False), encoding="utf-8"
    )

    first = importer.import_sessions(
        [_grown(("甲", "乙"), ("丙", "丁"))], companion_id="comp-a"
    )

    assert first.turns == 0                                  # 不重复写
    assert recorded == []
    assert importer.imported_turns("comp-a", "s1") == 2       # 基线 = 当前轮数
    assert any("旧格式" in w for w in first.warnings)

    # 迁移后新增的轮次能补上
    grown = importer.import_sessions(
        [_grown(("甲", "乙"), ("丙", "丁"), ("戊", "己"))], companion_id="comp-a"
    )

    assert grown.turns == 1
    assert recorded == [("戊", "己")]


def test_legacy_unknown_cursor_constant_is_negative() -> None:
    """`CURSOR_UNKNOWN` 与任何真实轮数都不冲突。"""
    assert CURSOR_UNKNOWN < 0


# =============================================================
# 按角色分作用域（AGENTS.md §8.2 的记忆隔离）
# =============================================================


def test_sessions_are_scoped_by_character(importer, store, recorded) -> None:
    """★ 酒馆里不同角色的剧情各进各的记忆——选谁只看得到谁的。

    旧实现把所有会话灌进同一个 `companion_id`：实测 4 个角色的聊天（21/1/27/5 条）
    全进了同一个陪伴对象，选任何一个角色都会召回别人的剧情。
    """
    scopes = {"甲": "tbp-aaa", "乙": "tbp-bbb"}

    result = importer.import_sessions(
        [
            _session("s1", ("user", "甲的事"), ("assistant", "嗯"), character="甲"),
            _session("s2", ("user", "乙的事"), ("assistant", "哦"), character="乙"),
        ],
        companion_id="fallback",
        character_scopes=scopes,
    )

    assert result.scopes == {"tbp-aaa": 1, "tbp-bbb": 1}
    assert result.warnings == []
    # 各自的记忆能召回
    assert store.recall("tbp-aaa", "甲的事").empty is False
    assert store.recall("tbp-bbb", "乙的事").empty is False
    # 回落作用域什么都没写
    assert store.recall("fallback", "甲的事").empty is True


def test_unmatched_character_falls_back_with_warning(importer, store) -> None:
    """角色卡被删了的会话回落到给定作用域，并在警告里说清数量（丢数据比串味更糟）。"""
    result = importer.import_sessions(
        [
            _session("s1", ("user", "甲的事"), ("assistant", "嗯"), character="甲"),
            _session("s2", ("user", "无名氏的事"), ("assistant", "哦"), character="没卡的角色"),
        ],
        companion_id="fallback",
        character_scopes={"甲": "tbp-aaa"},
    )

    assert result.scopes == {"tbp-aaa": 1, "fallback": 1}
    assert any("找不到对应角色卡" in w and "1 个会话" in w for w in result.warnings)
    assert store.recall("fallback", "无名氏的事").empty is False


def test_no_scopes_map_keeps_the_old_single_scope_behaviour(importer, store) -> None:
    """不给归属表时全部落到 `companion_id`（单角色场景 / 旧调用方的行为不变）。"""
    result = importer.import_sessions(
        [
            _session("s1", ("user", "甲"), ("assistant", "乙"), character="甲"),
            _session("s2", ("user", "丙"), ("assistant", "丁"), character="乙"),
        ],
        companion_id="comp-a",
    )

    assert result.scopes == {"comp-a": 2}
    assert result.warnings == []
    assert importer.imported_sessions("comp-a") == ["s1", "s2"]


def test_progress_is_tracked_per_scope(importer, recorded) -> None:
    """★ 进度按作用域各管各的：一个角色的新增对话不影响另一个角色的游标。"""
    scopes = {"甲": "tbp-aaa", "乙": "tbp-bbb"}
    importer.import_sessions(
        [_session("s1", ("user", "甲的事"), ("assistant", "嗯"), character="甲")],
        companion_id="fallback",
        character_scopes=scopes,
    )
    recorded.clear()

    # 甲又聊了两轮，乙没动
    result = importer.import_sessions(
        [
            _session(
                "s1",
                ("user", "甲的事"), ("assistant", "嗯"),
                ("user", "甲的新闻"), ("assistant", "真的"),
                character="甲",
            ),
            _session("s2", ("user", "乙的事"), ("assistant", "哦"), character="乙"),
        ],
        companion_id="fallback",
        character_scopes=scopes,
    )

    assert result.turns == 2                       # 甲的新一轮 + 乙的首次
    assert importer.imported_turns("tbp-aaa", "s1") == 2
    assert importer.imported_turns("tbp-bbb", "s2") == 1
    # 进度表按作用域分开落盘
    saved = json.loads(importer.state_path.read_text(encoding="utf-8"))
    assert saved["tbp-aaa"] == {"s1": 2}
    assert saved["tbp-bbb"] == {"s2": 1}


# =============================================================
# 待同步统计（界面上的数字必须等于点下去的写入量）
# =============================================================


def test_pending_progress_counts_new_turns(importer) -> None:
    """★ 待同步轮数与实际写入量**必然一致**——两者共用同一套游标规则。

    各算一套的话，用户会看到「说有 3 轮，点了却 0 轮」，而且无从判断是哪个环节错了。
    """
    sessions = [_grown(("甲", "乙"), ("丙", "丁"))]

    assert importer.pending_progress(sessions, companion_id="comp-a") == (2, 1)

    result = importer.import_sessions(sessions, companion_id="comp-a")

    assert (result.turns, result.sessions_imported) == (2, 1)
    assert importer.pending_progress(sessions, companion_id="comp-a") == (0, 0)


def test_pending_progress_tracks_the_growth(importer) -> None:
    importer.import_sessions([_grown(("甲", "乙"))], companion_id="comp-a")

    pending = importer.pending_progress(
        [_grown(("甲", "乙"), ("丙", "丁"), ("戊", "己"))], companion_id="comp-a"
    )

    assert pending == (2, 1)


def test_pending_progress_ignores_the_pending_user_turn(importer) -> None:
    """悬空轮不算待同步（它还没完成），与导入路径一致。"""
    assert importer.pending_progress(
        [_grown(("甲", ""))], companion_id="comp-a"
    ) == (0, 0)


def test_pending_progress_respects_scopes(importer) -> None:
    """待同步也按角色算：某个角色已同步过时，只有另一个角色算新增。"""
    scopes = {"甲": "tbp-aaa", "乙": "tbp-bbb"}
    importer.import_sessions(
        [_session("s1", ("user", "甲的事"), ("assistant", "嗯"), character="甲")],
        companion_id="fallback",
        character_scopes=scopes,
    )

    pending = importer.pending_progress(
        [
            _session("s1", ("user", "甲的事"), ("assistant", "嗯"), character="甲"),
            _session("s2", ("user", "乙的事"), ("assistant", "哦"), character="乙"),
        ],
        companion_id="fallback",
        character_scopes=scopes,
    )

    assert pending == (1, 1)  # 甲已是最新，乙还有 1 轮


def test_pending_progress_writes_nothing(importer, recorded) -> None:
    assert importer.pending_progress(
        [_grown(("甲", "乙"))], companion_id="comp-a"
    ) == (1, 1)
    assert recorded == []
    assert importer.imported_sessions("comp-a") == []
