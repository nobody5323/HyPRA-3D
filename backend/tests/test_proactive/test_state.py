"""节流状态存储单测：跨天滚动、原子写、坏文件容错。"""

import json
from datetime import datetime, timedelta, timezone

from app.proactive.state import ProactiveStateStore, local_now

TZ = timezone(timedelta(hours=8))


def _at(hour: int, *, day: int = 29) -> datetime:
    return datetime(2026, 9, day, hour, 0, tzinfo=TZ)


def test_initial_state_is_empty(tmp_path) -> None:
    store = ProactiveStateStore(tmp_path / "state.json")
    state = store.snapshot(now=_at(14))
    assert state.sent_today == 0
    assert state.last_proactive is None
    assert state.last_user_message is None


def test_note_proactive_increments_and_persists(tmp_path) -> None:
    path = tmp_path / "state.json"
    store = ProactiveStateStore(path)
    store.note_proactive(_at(14), fired_key="daily-greeting:2026-09-29")

    # 换一个实例读：验证真的落盘了（重启不失效是这一层存在的理由）
    reloaded = ProactiveStateStore(path).snapshot(now=_at(15))
    assert reloaded.sent_today == 1
    assert reloaded.last_proactive == _at(14)
    assert reloaded.fired["daily-greeting:2026-09-29"].startswith("2026-09-29")


def test_counter_rolls_over_on_new_day(tmp_path) -> None:
    """跨天归零在**读的时候**做——不依赖定时任务（用户可能关机过夜）。"""
    store = ProactiveStateStore(tmp_path / "state.json")
    store.note_proactive(_at(23, day=29))
    assert store.snapshot(now=_at(23, day=29)).sent_today == 1
    assert store.snapshot(now=_at(9, day=30)).sent_today == 0


def test_note_user_message_records_time(tmp_path) -> None:
    store = ProactiveStateStore(tmp_path / "state.json")
    store.note_user_message(_at(13))
    assert store.snapshot().last_user_message == _at(13)


def test_note_declined_records_key_but_not_quota(tmp_path) -> None:
    """「角色决定不说」不计入配额——否则「今天说了 3 次」会是个撒谎的数字。"""
    store = ProactiveStateStore(tmp_path / "state.json")
    store.note_declined(_at(14), fired_key="now-playing:2026-09-29:起风了")

    state = store.snapshot(now=_at(15))
    assert state.sent_today == 0
    assert state.last_proactive is None
    assert "now-playing:2026-09-29:起风了" in state.fired


def test_note_declined_without_key_is_noop(tmp_path) -> None:
    store = ProactiveStateStore(tmp_path / "state.json")
    store.note_declined(_at(14), fired_key="")
    assert store.snapshot().fired == {}


def test_fired_table_is_bounded(tmp_path) -> None:
    """去重表不能无限增长（否则状态文件会一直变大）。"""
    store = ProactiveStateStore(tmp_path / "state.json")
    for index in range(260):
        store.note_proactive(_at(14), fired_key=f"key-{index}")
    assert len(store.snapshot().fired) <= 200


def test_corrupt_file_falls_back_to_empty(tmp_path) -> None:
    """坏文件的后果是「可能多说几句」，而不是后端起不来。"""
    path = tmp_path / "state.json"
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    state = ProactiveStateStore(path).snapshot()
    assert state.sent_today == 0


def test_non_dict_json_falls_back_to_empty(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert ProactiveStateStore(path).snapshot().sent_today == 0


def test_reset_clears_everything(tmp_path) -> None:
    store = ProactiveStateStore(tmp_path / "state.json")
    store.note_proactive(_at(14), fired_key="k")
    store.reset()
    state = store.snapshot()
    assert state.sent_today == 0
    assert state.fired == {}


def test_naive_timestamp_does_not_crash(tmp_path) -> None:
    """手改过的状态文件可能不带时区——补本地时区，而不是相减时抛 TypeError。"""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"last_proactive_at": "2026-09-29T10:00:00"}), encoding="utf-8"
    )
    state = ProactiveStateStore(path).snapshot(now=_at(14))
    assert state.last_proactive is not None
    assert (state.last_proactive - _at(10)).total_seconds() == 0


def test_local_now_is_timezone_aware() -> None:
    """免打扰时段是用户墙上的钟：必须带时区，否则 UTC 下「深夜」会变成「下午」。"""
    assert local_now().tzinfo is not None
