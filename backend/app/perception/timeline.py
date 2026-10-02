"""行踪：主人最近在电脑上做了什么。

## 为什么需要它（「此刻」不够）

`snapshot.py` 的快照回答的是「**现在**在用哪个窗口」，TTL 只有 120 秒——
它刻意不留历史。于是角色只知道「此刻你在 VS Code」，
不知道「你已经连着写了三个小时代码」「刚刚从游戏里退出来」。

而后者才是能拿来**聊天**的东西：「忙完了？」「今天写了挺久啊」——
一句有由头的话，前提是它知道刚才发生了什么。这就是本模块存在的理由。

## 为什么按「段」记，不按「次」记

上报是每 30 秒一次。若逐次落盘，一天就是 2880 条「用户在用 Code.exe」，
既看不出结构，聚合时还要先去重。改成**段**（同一进程连续使用算一段）之后：

- 行踪天然是「09:10–11:40 在用 Code.exe」这种可读的形态；
- 数据量降到每天几十条；
- 「最近在忙什么」直接取最后几段就行，不需要额外推导。

## 三条隐私约束（与 `docs/proactive-multimodal.md` §4.6 同级）

| 约束 | 落地 |
| --- | --- |
| **默认不记窗口标题** | 只记**进程名**（`Code.exe`）。标题可能是一封邮件或一个病历页面，需要用户显式开 `perception_activity_include_title` 才记 |
| **不记内容** | 没有按键、没有剪贴板、没有 URL、没有文件路径 |
| **不进记忆库** | 轨迹只服务「此刻」的注入与触发判断；不写进温层/冷层，因此不会出现「我记得你上周三凌晨在听什么歌」 |
| **可一键清空** | `clear()` 删掉全部轨迹文件；关闭开关时也会调用它 |

## 空闲必须闭段

上报里的 `idle_seconds` 是「无键鼠输入的秒数」。人离开电脑时
前台窗口**仍然是那个窗口**——不处理的话会记出「用户连续 8 小时在用 VS Code」，
而那正是「行踪」最不能出的错（它会把「不在」读成「在」）。

所以空闲超过 `idle_gap_seconds` 时：闭段，且段的结束时刻取
`now - idle_seconds`（真正的最后一次操作），而不是「发现他离开的那一刻」。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.paths import data_path
from app.perception.clock import ensure_aware, local_now
from app.perception.models import DesktopContext

logger = logging.getLogger(__name__)

#: 轨迹数据的目录名（挂在 `data_root()` 下；开发形态 = `backend/data/perception/`）
DATA_SUBDIR = "perception"

#: 段的文件名前缀 / 当前段的文件名
_SEGMENT_PREFIX = "activity-"
_CURRENT_NAME = "activity-current.json"

#: 默认保留天数。**刻意短**：行踪是敏感数据，够用就好——
#: 「最近在做什么」只需要几天，而留得越久越像在攒档案。
DEFAULT_RETENTION_DAYS = 7

#: 默认空闲阈值（秒）：超过它就算「人不在」，闭段。
#: 取 300s：接个电话、去倒杯水不会断开；真的离开一定会断开。
DEFAULT_IDLE_GAP_SECONDS = 300.0

#: 同一个进程名连续使用多久算「新的一段」——用于防止一段跨越一整天。
#: 取 4 小时：超过它即使进程没变也断开，让「上午 / 下午」在行踪里可分辨。
DEFAULT_MAX_SEGMENT_SECONDS = 4 * 3600.0


# =============================================================
# 进程 → 类别
# =============================================================
#
# 类别是**机器可读的英文键**，中文标签在 `context.py` 渲染——
# 与「感知源只产出中性事实」是同一条约定（多一处中文就多一处漂移）。
#
# 会议 / 游戏 / 视频三类**直接复用 `busy.py` 的进程表**，不另抄一份：
# 那三张表已经为「忙碌判定」维护过一轮，抄一遍迟早两边不一致。
# 判断顺序也因此固定为「先复用 busy 的三类，再匹配本表」——
# 例如 `cloudmusic.exe` 同时出现在视频表与音乐里，先命中 busy 的视频类。

#: 类别键
CATEGORY_DEV = "dev"            # 写代码 / 调试
CATEGORY_OFFICE = "office"      # 文档 / 表格 / 邮件
CATEGORY_DESIGN = "design"      # 设计 / 剪辑
CATEGORY_BROWSER = "browser"    # 浏览器（内容未知，只能算「上网」）
CATEGORY_CHAT = "chat"          # 即时通讯
CATEGORY_MEDIA = "media"        # 看片 / 听歌
CATEGORY_GAME = "game"          # 游戏
CATEGORY_MEETING = "meeting"    # 会议 / 通话
CATEGORY_TOOL = "tool"          # 文件管理 / 系统工具
CATEGORY_OTHER = "other"        # 认不出来

_CATEGORY_TABLE: dict[str, frozenset[str]] = {
    CATEGORY_DEV: frozenset(
        {
            "code.exe",
            "code - insiders.exe",
            "cursor.exe",
            "windsurf.exe",
            "trae.exe",
            "zed.exe",
            "kiro.exe",
            "pycharm64.exe",
            "idea64.exe",
            "webstorm64.exe",
            "clion64.exe",
            "goland64.exe",
            "rider64.exe",
            "datagrip64.exe",
            "phpstorm64.exe",
            "devenv.exe",
            "eclipse.exe",
            "studio64.exe",
            "sublime_text.exe",
            "notepad++.exe",
            "vim.exe",
            "nvim.exe",
            "emacs.exe",
            "windowsterminal.exe",
            "wt.exe",
            "powershell.exe",
            "pwsh.exe",
            "cmd.exe",
            "git-bash.exe",
            "mintty.exe",
            "conhost.exe",
            "docker desktop.exe",
            "postman.exe",
            "insomnia.exe",
            "hbuilderx.exe",
            "arduino ide.exe",
            "unity.exe",
            "godot.exe",
            "rstudio.exe",
            "matlab.exe",
            "jupyter-notebook.exe",
            "dbeaver.exe",
            "navicat.exe",
            "sourcetree.exe",
            "github desktop.exe",
        }
    ),
    CATEGORY_OFFICE: frozenset(
        {
            "winword.exe",
            "excel.exe",
            "powerpnt.exe",
            "outlook.exe",
            "onenote.exe",
            "msaccess.exe",
            "wps.exe",
            "et.exe",
            "wpp.exe",
            "acrobat.exe",
            "acrord32.exe",
            "foxitreader.exe",
            "sumatrapdf.exe",
            "typora.exe",
            "obsidian.exe",
            "notion.exe",
            "logseq.exe",
        }
    ),
    CATEGORY_DESIGN: frozenset(
        {
            "photoshop.exe",
            "illustrator.exe",
            "indesign.exe",
            "figma.exe",
            "sketch.exe",
            "blender.exe",
            "3dsmax.exe",
            "maya.exe",
            "cinema 4d.exe",
            "adobe premiere pro.exe",
            "afterfx.exe",
            "resolve.exe",
            "coreldrw.exe",
            "krita.exe",
            "clipstudio.exe",
            "sai2.exe",
            "procreate.exe",
            "xmind.exe",
            "mindmaster.exe",
            "axure rp.exe",
        }
    ),
    CATEGORY_BROWSER: frozenset(
        {
            "chrome.exe",
            "msedge.exe",
            "firefox.exe",
            "brave.exe",
            "opera.exe",
            "vivaldi.exe",
            "arc.exe",
            "360se.exe",
            "360chrome.exe",
            "qqbrowser.exe",
            "sogouexplorer.exe",
            "ucbrowser.exe",
            "liebao.exe",
            "maxthon.exe",
        }
    ),
    CATEGORY_CHAT: frozenset(
        {
            "wechat.exe",
            "weixin.exe",
            "qq.exe",
            "tim.exe",
            "telegram.exe",
            "whatsapp.exe",
            "slack.exe",
            "momo.exe",
        }
    ),
    CATEGORY_TOOL: frozenset(
        {
            "explorer.exe",
            "everything.exe",
            "7zfm.exe",
            "winrar.exe",
            "bandizip.exe",
            "taskmgr.exe",
            "snipaste.exe",
            "pixpin.exe",
            "sharex.exe",
            "powertoys.exe",
            "cmd.exe",
            "control.exe",
            "systemsettings.exe",
            "calc.exe",
        }
    ),
}

#: 音乐播放器（归到「听歌」，与看片同属 media 但在渲染时可以区分）
MUSIC_PROCESSES: frozenset[str] = frozenset(
    {
        "spotify.exe",
        "qqmusic.exe",
        "cloudmusic.exe",
        "kugou.exe",
        "kuwo.exe",
        "foobar2000.exe",
        "aimp.exe",
        "musicbee.exe",
    }
)


def categorize(process: str) -> str:
    """进程名 → 类别键（纯函数，认不出来就是 `other`）。

    判断顺序有意固定：**先会议 / 游戏 / 视频（复用 `busy.py`），再本表**。
    理由：那三张表是「不该打扰」的判据来源，行踪里的类别应与它一致——
    同一个进程在两处被分成不同类别，迟早会出现「忙碌判定说在开会、
    行踪说在聊天」这种自相矛盾。
    """
    name = (process or "").strip().lower()
    if not name:
        return CATEGORY_OTHER
    # 延迟导入：busy 是纯数据模块，这里只是复用它的三张表
    from app.perception.busy import GAME_PROCESSES, MEETING_PROCESSES, VIDEO_PROCESSES

    if name in MEETING_PROCESSES:
        return CATEGORY_MEETING
    if name in GAME_PROCESSES:
        return CATEGORY_GAME
    if name in VIDEO_PROCESSES or name in MUSIC_PROCESSES:
        return CATEGORY_MEDIA
    for category, table in _CATEGORY_TABLE.items():
        if name in table:
            return category
    return CATEGORY_OTHER


#: 「在做事」的类别（用于「从工作切到娱乐」这类判断，见 `proactive/triggers.py`）
FOCUS_CATEGORIES: frozenset[str] = frozenset(
    {CATEGORY_DEV, CATEGORY_OFFICE, CATEGORY_DESIGN, CATEGORY_MEETING}
)

#: 「在放松」的类别
LEISURE_CATEGORIES: frozenset[str] = frozenset(
    {CATEGORY_MEDIA, CATEGORY_GAME}
)


# =============================================================
# 数据模型
# =============================================================


@dataclass(frozen=True)
class ActivitySegment:
    """一段行踪（同一进程连续使用）。字段全是字符串/数字，便于直接落 JSON。"""

    process: str
    category: str
    #: 窗口标题（**默认空**：只有用户显式开了 `perception_activity_include_title` 才有）
    title: str
    #: ISO 8601（带时区）
    started_at: str
    ended_at: str
    #: 是否仍在进行。
    #:
    #: 早先是用「起止时刻相等」表示的——那样段一旦被刷新（`ended_at` 往后推），
    #: 它就不再算「进行中」，于是「当前在用什么」与画像里的跨零点摊分同时失效。
    #: 改成显式字段后有个必须守住的约束：**只有内存里的当前段与
    #: `activity-current.json` 会是 True，写进日文件的一定是 False**
    #: （见 `_close_locked` / `_read_range_locked`）——否则会出现
    #: 「布尔说是开着、时间却停在两小时前」那种不一致。
    ongoing: bool = False

    @property
    def start(self) -> datetime:
        return ensure_aware(datetime.fromisoformat(self.started_at))

    @property
    def end(self) -> datetime:
        return ensure_aware(datetime.fromisoformat(self.ended_at))

    def seconds(self, *, now: datetime | None = None) -> float:
        """本段时长（秒）。仍开着的段用 `now` 兜底。"""
        end = ensure_aware(now or local_now()) if self.ongoing else self.end
        return max(0.0, (end - self.start).total_seconds())


def _segment_of(
    context: DesktopContext, *, started_at: datetime, include_title: bool
) -> ActivitySegment | None:
    """从一次上报里开一段（进程名为空时返回 None——认不出在做什么就不记）。"""
    process = (context.foreground_process or "").strip()
    if not process:
        return None
    moment = started_at.isoformat()
    return ActivitySegment(
        process=process,
        category=categorize(process),
        # 标题按开关决定留不留：关着时**根本不写进对象**，而不是「记了不用」
        title=(context.foreground_title or "").strip() if include_title else "",
        started_at=moment,
        ended_at=moment,
        ongoing=True,
    )


# =============================================================
# 存储
# =============================================================


class ActivityTimeline:
    """活动轨迹（进程内单例；数据落 `data/perception/`）。

    线程安全：写入来自 API 路由（事件循环线程），读取来自对话图执行线程
    与主动链路后台任务。读写共用一把锁——数据量小、冲突极低。
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        idle_gap_seconds: float = DEFAULT_IDLE_GAP_SECONDS,
        include_title: bool = False,
        max_segment_seconds: float = DEFAULT_MAX_SEGMENT_SECONDS,
    ) -> None:
        self._root = Path(root) if root is not None else data_path(DATA_SUBDIR)
        self.retention_days = max(1, int(retention_days))
        self.idle_gap_seconds = max(1.0, float(idle_gap_seconds))
        self.include_title = bool(include_title)
        self.max_segment_seconds = max(60.0, float(max_segment_seconds))
        self._lock = threading.Lock()
        #: 当前开着的段（内存里），以及它属于哪一天（决定落哪个文件）
        self._current: ActivitySegment | None = None
        self._current_day: str = ""
        self._current_loaded = False

    # ---------- 路径 ----------

    def _segment_file(self, day: str) -> Path:
        return self._root / f"{_SEGMENT_PREFIX}{day}.jsonl"

    def _current_file(self) -> Path:
        return self._root / _CURRENT_NAME

    # ---------- 写入 ----------

    def observe(self, context: DesktopContext, *, now: datetime | None = None) -> None:
        """记录一次桌面情景上报（**由 `POST /perception/desktop` 调用**）。

        三件事，按顺序：
        1. 空闲超过阈值 → 闭段（结束时刻取「最后一次操作」，不是「发现他离开」）；
        2. 进程变了 / 段太长了 / 跨天了 → 闭段并开新段；
        3. 否则只把当前段的结束时刻往后推。

        任何异常都吞掉：行踪记不上只是少了主动开口的由头，
        绝不该让桌面情景上报（它还要写感知快照）整体失败。
        """
        moment = now or local_now()
        try:
            with self._lock:
                self._ensure_loaded()
                self._observe_locked(context, moment)
        except Exception as exc:  # noqa: BLE001 - 见 docstring
            logger.warning("行踪记录失败（已忽略）：%s", exc)

    def _observe_locked(self, context: DesktopContext, moment: datetime) -> None:
        idle = float(context.idle_seconds or 0.0)
        if idle >= self.idle_gap_seconds:
            # 人不在：把当前段按「最后一次操作」收尾。**不开新段**——
            # 此刻并没有可观测的活动。
            if self._current is not None:
                self._close_locked(end=moment - timedelta(seconds=idle))
            return

        current = self._current
        if current is None:
            self._open_locked(context, moment)
            return

        same_process = current.process.lower() == (context.foreground_process or "").strip().lower()
        too_long = (moment - current.start).total_seconds() >= self.max_segment_seconds
        new_day = moment.strftime("%Y-%m-%d") != self._current_day
        if not same_process or too_long or new_day:
            self._close_locked(end=moment)
            self._open_locked(context, moment)
            return

        # 同一进程：只把结束时刻往后推（标题按开关刷新——同一进程换了文档时，
        # 段不重开但标题要跟上）
        self._current = ActivitySegment(
            process=current.process,
            category=current.category,
            title=self._title_of(context) or current.title,
            started_at=current.started_at,
            ended_at=moment.isoformat(),
            ongoing=True,
        )
        self._persist_current_locked()

    def _title_of(self, context: DesktopContext) -> str:
        """按开关决定要不要留标题。关着时永远返回空串（不是「记了不用」）。"""
        if not self.include_title:
            return ""
        return (context.foreground_title or "").strip()

    def _open_locked(self, context: DesktopContext, moment: datetime) -> None:
        segment = _segment_of(context, started_at=moment, include_title=self.include_title)
        if segment is None:
            return
        self._current = segment
        self._current_day = moment.strftime("%Y-%m-%d")
        self._persist_current_locked()

    def _close_locked(self, *, end: datetime) -> None:
        """闭段并落盘。`end` 早于开始时刻时按开始时刻收（不产生负时长的段）。"""
        current = self._current
        self._current = None
        self._current_day = ""
        self._drop_current_file_locked()
        if current is None:
            return
        finished = end if end > current.start else current.start
        segment = ActivitySegment(
            process=current.process,
            category=current.category,
            title=current.title,
            started_at=current.started_at,
            ended_at=finished.isoformat(),
            # 写进日文件的一定是「已结束」——见 `ActivitySegment.ongoing` 的说明
            ongoing=False,
        )
        if segment.seconds() < 1.0:
            # 不足一秒的段是抖动（窗口一闪而过），不记——记了只会污染聚合
            return
        self._append_locked(segment)
        self._prune_locked()

    def _append_locked(self, segment: ActivitySegment) -> None:
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            day = segment.started_at[:10]
            with self._segment_file(day).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(segment), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("行踪落盘失败（已忽略）：%s", exc)

    def _persist_current_locked(self) -> None:
        """把「当前段」单独落一个小文件。

        为什么不能只等闭段再写：用户在 VS Code 里连着写三小时，
        那三小时里一条记录都没有——画像和「最近在忙什么」会同时读到空。
        每 30 秒原子覆盖一个几十字节的文件，代价可以忽略。
        """
        current = self._current
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            path = self._current_file()
            if current is None:
                path.unlink(missing_ok=True)
                return
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(asdict(current), ensure_ascii=False) + "\n", encoding="utf-8"
            )
            temporary.replace(path)
        except OSError as exc:
            logger.warning("当前行踪写盘失败（已忽略）：%s", exc)

    def _drop_current_file_locked(self) -> None:
        try:
            self._current_file().unlink(missing_ok=True)
        except OSError:  # noqa: BLE001 - 删不掉不该影响主流程
            pass

    def _prune_locked(self) -> None:
        """删掉超出保留期的日文件。"""
        cutoff = (local_now() - timedelta(days=self.retention_days)).strftime("%Y-%m-%d")
        try:
            for path in self._root.glob(f"{_SEGMENT_PREFIX}*.jsonl"):
                day = path.stem[len(_SEGMENT_PREFIX) :]
                if day and day < cutoff:
                    path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("行踪清理失败（已忽略）：%s", exc)

    # ---------- 读取 ----------

    def _ensure_loaded(self) -> None:
        """首次访问时把「当前段」从磁盘捡回来（进程重启不丢正在进行的这一段）。"""
        if self._current_loaded:
            return
        self._current_loaded = True
        path = self._current_file()
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            segment = ActivitySegment(
                process=str(raw.get("process") or ""),
                category=str(raw.get("category") or CATEGORY_OTHER),
                title=str(raw.get("title") or ""),
                started_at=str(raw.get("started_at") or ""),
                ended_at=str(raw.get("ended_at") or ""),
                # current.json 里存的就是**正在进行的这一段**，因此恒为 True
                ongoing=True,
            )
            if segment.process and segment.started_at:
                self._current = segment
                self._current_day = segment.started_at[:10]
        except (OSError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            logger.warning("当前行踪读不动，已忽略：%s", exc)

    def segments(self, *, hours: float = 24.0, now: datetime | None = None) -> list[ActivitySegment]:
        """取最近 `hours` 小时内的段（按开始时刻升序，含仍在进行的一段）。"""
        moment = now or local_now()
        since = moment - timedelta(hours=max(0.1, float(hours)))
        with self._lock:
            self._ensure_loaded()
            rows = self._read_range_locked(since=since, until=moment)
            if self._current is not None:
                rows.append(self._current)
        # 只留与窗口有交集的段（跨窗口边界的段按交集保留）
        visible = [row for row in rows if row.end >= since and row.start <= moment]
        visible.sort(key=lambda row: row.started_at)
        return visible

    def current(self, *, now: datetime | None = None) -> ActivitySegment | None:
        """当前开着的段（没有则 None）。"""
        with self._lock:
            self._ensure_loaded()
            return self._current

    def summary(self, *, hours: float = 3.0, now: datetime | None = None) -> ActivitySummary:
        """取一份给触发器用的不可变摘要（段 + 「刚回来」的离开时长）。"""
        moment = now or local_now()
        return ActivitySummary(
            segments=tuple(self.segments(hours=hours, now=moment)),
            away_seconds=self.away_before_current(now=moment),
        )

    def _read_range_locked(self, *, since: datetime, until: datetime) -> list[ActivitySegment]:
        """读 `since` 覆盖到的那些日文件（最多往回多读一天，防跨零点漏段）。"""
        days: list[str] = []
        cursor = since.date()
        last = until.date()
        while cursor <= last:
            days.append(cursor.strftime("%Y-%m-%d"))
            cursor += timedelta(days=1)
        # 往前多读一天：一段可能从昨天开始、今天才结束
        days.insert(0, (since.date() - timedelta(days=1)).strftime("%Y-%m-%d"))

        rows: list[ActivitySegment] = []
        for day in days:
            path = self._segment_file(day)
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("行踪读取失败（已跳过 %s）：%s", path.name, exc)
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    rows.append(
                        ActivitySegment(
                            process=str(raw.get("process") or ""),
                            category=str(raw.get("category") or CATEGORY_OTHER),
                            title=str(raw.get("title") or ""),
                            started_at=str(raw.get("started_at") or ""),
                            ended_at=str(raw.get("ended_at") or ""),
                            # 日文件里只有**已结束**的段。即使文件被手改出一个
                            # ongoing:true 也不认——那样会凭空多出一段「永远在跑」的行踪
                            ongoing=False,
                        )
                    )
                except (json.JSONDecodeError, AttributeError, TypeError):
                    # 单行坏了跳过，不因为一行脏数据丢掉整天的行踪
                    continue
        return [row for row in rows if row.started_at and row.ended_at]

    def away_before_current(self, *, now: datetime | None = None) -> float:
        """当前这段开始前，用户离开了多久（秒）；不足 1 秒或拿不到则返回 0。

        判据：当前段刚开始（`RETURN_WINDOW` 秒内）且它之前存在一段已闭的段——
        两者之间的空隙就是「离开的时长」。

        这正是「久别重逢」的由头：「他离开了两个小时，刚回来」。
        没有这个判据的话，`observe` 每 30 秒都会看到「有新段」，从而反复误判成「刚回来」。
        """
        moment = now or local_now()
        with self._lock:
            self._ensure_loaded()
            current = self._current
            if current is None:
                return 0.0
            if (moment - current.start).total_seconds() > RETURN_WINDOW_SECONDS:
                return 0.0
            rows = self._read_range_locked(
                since=current.start - timedelta(days=1), until=current.start
            )
        previous = [row for row in rows if row.end <= current.start]
        if not previous:
            return 0.0
        last = max(previous, key=lambda row: row.ended_at)
        gap = (current.start - last.end).total_seconds()
        return gap if gap >= 1.0 else 0.0

    # ---------- 清理 ----------

    def clear(self) -> int:
        """删掉全部行踪（界面「清除行踪记录」用）。返回**真正删掉**的文件数。

        返回值必须诚实：界面上会显示「已清除 N 项记录」，而这个数字同时是
        「本来就没有」与「删干净了」的区分依据（`removed == 0` 时提示语不同）。
        所以只数真正存在的文件——`unlink(missing_ok=True)` 对不存在的路径
        也会成功，直接计数会把「什么都没删」报成「删了 1 个」。
        """
        with self._lock:
            self._current = None
            self._current_day = ""
            self._current_loaded = True
            removed = 0
            try:
                if not self._root.is_dir():
                    return 0
                # `activity-*` 已经涵盖 `activity-current.json`（同前缀），
                # 因此只 glob 一次——再单独删一次会把它数成两个文件
                for path in self._root.glob(f"{_SEGMENT_PREFIX}*"):
                    if not path.is_file():
                        continue
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        continue
            except OSError as exc:
                logger.warning("清除行踪失败：%s", exc)
            return removed


#: 「刚回来」的时间窗（秒）：当前段开始后多久之内算「刚刚回来」
RETURN_WINDOW_SECONDS = 180.0


@dataclass(frozen=True)
class ActivitySummary:
    """给触发器用的行踪摘要（**不可变**）。

    为什么单独抽一个不可变对象：触发器的 `evaluate` 必须是纯函数
    （只读上下文、返回意图或 None，见 `proactive/models.py`），
    所以 runner 先把行踪查好、冻成这个对象再放进 `TriggerContext`——
    触发器因此不必持有轨迹单例、也不必做任何 IO，可以逐条单测。
    """

    #: 窗口内的段（按开始时刻升序，最后一段可能仍在进行）
    segments: tuple[ActivitySegment, ...] = ()
    #: 若用户「刚刚回来」，这里是离开的时长（秒）；否则 0
    away_seconds: float = 0.0

    @property
    def current(self) -> ActivitySegment | None:
        """仍在进行的那一段（没有则 None）。"""
        for segment in reversed(self.segments):
            if segment.ongoing:
                return segment
        return None

    @property
    def previous(self) -> ActivitySegment | None:
        """当前段的上一段（判断「从什么切到什么」用）。"""
        current = self.current
        if current is None:
            return None
        index = self.segments.index(current)
        return self.segments[index - 1] if index > 0 else None

    @property
    def latest(self) -> ActivitySegment | None:
        """窗口内最后一段（不管是否还在进行）。"""
        return self.segments[-1] if self.segments else None


# =============================================================
# 进程内单例
# =============================================================

_timeline: ActivityTimeline | None = None
_timeline_signature: tuple = ()


def _signature_of(settings) -> tuple:
    """影响轨迹行为的配置项。改了才重建单例（见 `get_timeline`）。"""
    return (
        str(settings.perception_activity_retention_days),
        str(settings.perception_activity_idle_gap_seconds),
        str(settings.perception_activity_include_title),
        str(settings.perception_activity_max_segment_hours),
    )


def get_timeline(*, settings=None) -> ActivityTimeline:
    """取轨迹单例。

    **配置真的变了才重建**（与 `api/perception.py` 的 provider 缓存同一口径）：
    保留天数、空闲阈值、是否记标题都可在界面上改，改了就重建；
    没变就复用，避免每次上报都 new 一个对象（那会丢掉内存里的当前段）。
    """
    global _timeline, _timeline_signature
    if settings is None:
        from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

        settings = get_settings()
    signature = _signature_of(settings)
    if _timeline is None or _timeline_signature != signature:
        _timeline = ActivityTimeline(
            retention_days=settings.perception_activity_retention_days,
            idle_gap_seconds=settings.perception_activity_idle_gap_seconds,
            include_title=settings.perception_activity_include_title,
            max_segment_seconds=float(settings.perception_activity_max_segment_hours) * 3600.0,
        )
        _timeline_signature = signature
    return _timeline


def set_timeline(timeline: ActivityTimeline | None) -> None:
    """替换/重置轨迹单例（测试注入用）。

    **注入时必须把签名对齐当前配置**：否则下一次 `get_timeline()` 会认为
    「配置变了」（签名是空的）而按配置重建一个真实现，把注入的替身覆盖掉——
    表现是「测试里注入的临时目录不生效，数据写进了开发机」。
    这与 `api/perception.py: set_asr_provider` 踩过的是同一个坑。
    """
    global _timeline, _timeline_signature
    _timeline = timeline
    if timeline is None:
        _timeline_signature = ()
        return
    from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

    _timeline_signature = _signature_of(get_settings())


def sync_activity_switch(*, settings=None) -> int:
    """把「行踪开关」的当前状态同步到磁盘：关掉时清空全部记录。返回清掉的文件数。

    ## 为什么不能只在 `POST /perception/desktop` 里清

    那条路要求**桌宠端正在跑**。用户完全可能「先在程序控制台关掉开关，
    再关掉桌宠窗」——那时最后一次上报已经发生过，文件会一直留在盘上。

    而「关掉开关」的语义是「不再记」，**留着已经记下的内容与「不再记」是两回事**：
    用户关掉它的动机通常就是「我不想要这些了」，而不是「从今往后别再写」。
    所以开关一变就要立刻对账，不能等下一次上报。

    刻意做成幂等的：开着的时候什么都不做，关着的时候清空——
    因此可以放心地在「配置变更」与「每次上报」两处都调它。
    """
    from app.config import get_settings  # noqa: PLC0415 - 避免模块级循环

    resolved = settings if settings is not None else get_settings()
    if resolved.perception_activity_enabled:
        return 0
    removed = get_timeline(settings=resolved).clear()
    # 画像缓存拿着清空前的聚合结果，必须一起作废——
    # 否则界面上「它对你的印象」还会继续显示已经被删掉的行踪。
    from app.perception.profile import reset_profile_cache  # noqa: PLC0415

    reset_profile_cache()
    return removed
