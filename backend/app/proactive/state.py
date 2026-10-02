"""主动链路的节流状态（落盘，重启后不失效）。

## 为什么必须落盘

闸门③（冷却 90 分钟）与闸门④（每日 6 条）依赖「上次主动开口是什么时候」
「今天已经说了几次」。这些如果只在内存里，**每次重启后端都会忘掉**——
表现是「重启一下就又开始主动找你」，而这恰恰是用户最反感的行为。

## 日期滚动在**读的时候**做

`sent_today` 的归零不靠定时任务，而是在 `snapshot()` 里比对日期。
理由：定时任务要求进程一直活着；而用户完全可能关机过夜。
读时比对是幂等的，且不需要额外调度。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# `local_now` / `ensure_aware` 的实现已上移到 `app/perception/clock.py`
# ——时钟是感知层的事（「现在几点」本身就是一条感知事实），而本模块只是
# 其中一个消费者。在这里**原样再导出**：既有的 `from app.proactive.state import
# local_now` 调用方（triggers / runner / gate / 测试）一行都不用改，
# 而实现只有一个来源，不会出现「两处各写一份、时区口径慢慢漂开」。
from app.perception.clock import ensure_aware, local_now  # noqa: F401

logger = logging.getLogger(__name__)


def _to_iso(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _from_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # 历史数据可能没带时区（手改文件 / 早期版本）：补本地时区，
    # 否则与带时区的 now 相减会抛 TypeError
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


@dataclass
class ProactiveState:
    """节流状态。字段全是字符串/数字，便于直接落 JSON。"""

    last_proactive_at: str = ""
    last_user_message_at: str = ""
    sent_date: str = ""
    sent_today: int = 0
    #: 触发器去重：`dedupe_key` → 上次触发的 ISO 时刻
    fired: dict[str, str] = field(default_factory=dict)

    @property
    def last_proactive(self) -> datetime | None:
        return _from_iso(self.last_proactive_at)

    @property
    def last_user_message(self) -> datetime | None:
        return _from_iso(self.last_user_message_at)


class ProactiveStateStore:
    """节流状态的读写（原子写 + 读时滚动日期）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._state: ProactiveState | None = None

    # ---------- 读 ----------

    def _load(self) -> ProactiveState:
        if self._state is not None:
            return self._state
        if not self.path.is_file():
            self._state = ProactiveState()
            return self._state
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 文件坏了就按「从未主动开口」处理。注意这与「忽略」不同：
            # 坏文件的后果是**多说了几句**，而不是后端起不来——
            # 一个记录节流的小文件不该有这个权力。
            logger.warning("主动链路状态文件读不动，按初始状态处理：%s", self.path)
            self._state = ProactiveState()
            return self._state
        if not isinstance(raw, dict):
            self._state = ProactiveState()
            return self._state
        fired = raw.get("fired")
        self._state = ProactiveState(
            last_proactive_at=str(raw.get("last_proactive_at") or ""),
            last_user_message_at=str(raw.get("last_user_message_at") or ""),
            sent_date=str(raw.get("sent_date") or ""),
            sent_today=int(raw.get("sent_today") or 0),
            fired={str(k): str(v) for k, v in fired.items()} if isinstance(fired, dict) else {},
        )
        return self._state

    def snapshot(self, *, now: datetime | None = None) -> ProactiveState:
        """当前状态（**已做日期滚动**：跨天后 `sent_today` 归零）。"""
        with self._lock:
            state = self._load()
            today = (now or local_now()).strftime("%Y-%m-%d")
            if state.sent_date != today:
                state.sent_date = today
                state.sent_today = 0
            return state

    # ---------- 写 ----------

    def _persist(self, state: ProactiveState) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
            temporary.write_text(
                json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            # 写失败只记日志：节流状态丢了的后果是「可能多说一句」，
            # 绝不该让主动链路抛异常（它跑在后台任务里，异常会静默吞掉调度）
            logger.warning("主动链路状态写盘失败（已忽略）：%s", exc)

    def note_user_message(self, when: datetime | None = None) -> None:
        """记录「用户说话了」（由对话路由调用）。

        这是闸门⑤的依据。**每轮对话都会写一次盘**——文件只有几百字节，
        写入在亚毫秒级；而把它做成防抖会引入「刚重启就以为用户很久没说话」
        的窗口期，那个代价更大。
        """
        with self._lock:
            state = self._load()
            state.last_user_message_at = _to_iso(when or local_now())
            self._persist(state)

    def note_proactive(self, when: datetime | None = None, *, fired_key: str = "") -> None:
        """记录「刚主动开过一次口」：更新冷却锚点、当日计数与去重表。"""
        moment = when or local_now()
        with self._lock:
            state = self._load()
            today = moment.strftime("%Y-%m-%d")
            if state.sent_date != today:
                state.sent_date = today
                state.sent_today = 0
            state.last_proactive_at = _to_iso(moment)
            state.sent_today += 1
            if fired_key:
                state.fired[fired_key] = _to_iso(moment)
                # 去重表不能无限增长：只保留最近 200 条（按写入顺序，
                # dict 保序，最早的在前）
                while len(state.fired) > 200:
                    state.fired.pop(next(iter(state.fired)))
            self._persist(state)

    def note_declined(self, when: datetime | None = None, *, fired_key: str = "") -> None:
        """记录「这次意图评估过了，但角色决定不说」。

        **只记去重键，不更新冷却锚点、不计数**——它毕竟没有开口，
        计入配额会让「每日 6 条」变成一个撒谎的数字（界面显示今天说了 3 次，
        而用户一条都没收到）。

        但去重键**必须记**：不记的话，下一轮调度（60 秒后）会再次评估出同一个意图、
        再调一次模型，形成「每分钟烧一次 LLM 调用、用户什么都收不到」的死循环。
        """
        if not fired_key:
            return
        moment = when or local_now()
        with self._lock:
            state = self._load()
            state.fired[fired_key] = _to_iso(moment)
            while len(state.fired) > 200:
                state.fired.pop(next(iter(state.fired)))
            self._persist(state)

    def reset(self) -> None:
        """清空状态（界面「重置主动沟通节流」用）。"""
        with self._lock:
            self._state = ProactiveState()
            self._persist(self._state)


#: 进程内单例（按路径缓存；测试可用 `set_state_store` 替换）
_store: ProactiveStateStore | None = None
_store_path: str = ""


def get_state_store(path: str) -> ProactiveStateStore:
    """取状态存储单例（路径变了就重建，便于测试与配置切换）。"""
    global _store, _store_path
    if _store is None or _store_path != str(path):
        _store = ProactiveStateStore(path)
        _store_path = str(path)
    return _store


def set_state_store(store: ProactiveStateStore | None) -> None:
    """替换/重置状态存储（测试注入用）。"""
    global _store, _store_path
    _store = store
    _store_path = str(store.path) if store is not None else ""
