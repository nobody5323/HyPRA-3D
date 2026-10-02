"""感知事件的数据模型（中性结构化事实）。

字段刻意保持「机器可读」：**不含任何中文句子**。渲染成提示词是
`app/perception/context.py` 的事——那样措辞只在一个地方定义，
而触发器的判断逻辑也不必去解析中文。
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PerceptionEvent(BaseModel):
    """一条感知事实。"""

    source: str = Field(description="感知源：asr | vision | desktop")
    kind: str = Field(
        description=(
            "事实种类：user_speech | now_playing | foreground_window | idle | "
            "battery | local_time | user_image"
        )
    )
    payload: dict = Field(
        default_factory=dict,
        description="中性字段（如 {'title': '...', 'artist': '...'}），不含中文句子",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度")
    observed_at: datetime = Field(default_factory=_now, description="观测时刻")
    ttl_seconds: int = Field(
        default=300,
        ge=1,
        description=(
            "存活秒数。**感知是易失的**：5 分钟前听过的歌不该继续出现在上下文里，"
            "过期即丢——这是它与记忆的本质区别（记忆要留，感知要忘）"
        ),
    )

    def expired(self, *, now: datetime | None = None) -> bool:
        """是否已过期。"""
        moment = now or _now()
        return (moment - self.observed_at).total_seconds() > self.ttl_seconds


class DesktopContext(BaseModel):
    """桌面情景上报（Electron 主进程采集 → 渲染层 → `POST /perception/desktop`）。

    **隐私取舍**：`foreground_title` 与 `foreground_process` 都会采集
    （后者是「忙碌判定」必需的——全屏游戏/会议软件要靠进程名识别），
    但**窗口标题默认不渲染进提示词**（见 `context.py`）：歌名是用户主动播放的、
    可以聊；窗口标题可能是一封邮件或一个病历页面，不该被模型看见。
    用户显式打开开关后才渲染。
    """

    #: 系统媒体（Windows SMTC）：正在播放
    now_playing_title: str = ""
    now_playing_artist: str = ""
    #: 前台窗口（标题**默认不注入提示词**）
    foreground_title: str = ""
    foreground_process: str = ""
    #: 用户空闲秒数（无键鼠输入）
    idle_seconds: float = 0.0
    #: 电量
    battery_percent: float | None = None
    battery_charging: bool | None = None
    #: 客户端本地时间（HH:MM，24 小时制）；由客户端给，避免主机与用户时区不一致
    local_time: str = ""
    #: 客户端本地的日期（YYYY-MM-DD）
    local_date: str = ""

    def is_idle(self, threshold_seconds: float) -> bool:
        """是否已空闲超过阈值（「用户不在」的判据）。"""
        return self.idle_seconds >= threshold_seconds
