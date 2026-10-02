"""忙碌判定：什么时候**不该**开口。

设计见 `docs/proactive-multimodal.md` §5.3 的闸门⑥。**纯函数**，
进程表是可注入参数，因此可以逐条单测。

## 为什么只用「进程名」判据，不用「窗口标题」

进程名是**类别**（会议软件 / 游戏），窗口标题是**内容**（可能是一封邮件、
一个病历页面）。用标题做功能判定，等于把隐私变成功能依赖——
而这个功能的失败模式（该安静时开口）恰恰是最让人反感的。

## 为什么**不**用「全屏」当通用判据

全屏是弱信号：VS Code 全屏、浏览器全屏看文档都很常见。用它会得到一个
「用户一全屏，陪伴就哑掉」的行为——而那正是「因为误判把功能整体哑掉」，
比多说一句话糟糕得多（见 `runner._is_busy` 的取舍说明）。

因此只认**高置信度**的进程：会议软件、游戏、全屏视频播放器。
"""

from __future__ import annotations

from app.perception.models import DesktopContext

#: 会议 / 通话类进程（用户正在跟人说话，此刻插嘴是明确打扰）
MEETING_PROCESSES: frozenset[str] = frozenset(
    {
        "zoom.exe",
        "teams.exe",
        "ms-teams.exe",
        "wemeetapp.exe",  # 腾讯会议
        "dingtalk.exe",
        "feishu.exe",
        "lark.exe",
        "webexmta.exe",
        "skype.exe",
        "discord.exe",  # 语音频道常用，保守起见算会议
        "voov.exe",
    }
)

#: 游戏进程（正在对局时开口会打断操作）
GAME_PROCESSES: frozenset[str] = frozenset(
    {
        "steam.exe",
        "steamwebhelper.exe",
        "epicgameslauncher.exe",
        "battle.net.exe",
        "genshinimpact.exe",
        "yuanshen.exe",
        "starrail.exe",
        "league of legends.exe",
        "valorant.exe",
        "cs2.exe",
        "dota2.exe",
        "overwatch.exe",
        "warthunder.exe",
        "escapefromtarkov.exe",
        "minecraft.exe",
        "hmcl.exe",
        "pcl2.exe",
        "warframe.x64.exe",
        "eldenring.exe",
        "baldursgate3.exe",
    }
)

#: 全屏视频播放（正在看片子：不是不能说话，而是说了也没人在看）
VIDEO_PROCESSES: frozenset[str] = frozenset(
    {
        "potplayer.exe",
        "vlc.exe",
        "mpv.exe",
        "mpc-hc64.exe",
        "bilibili.exe",
        "cloudmusic.exe",  # 桌面歌词/全屏播放模式
    }
)

#: 默认进程表（三类的并集）
BUSY_PROCESSES: frozenset[str] = MEETING_PROCESSES | GAME_PROCESSES | VIDEO_PROCESSES


def is_busy(
    desktop: DesktopContext | None,
    *,
    processes: frozenset[str] | None = None,
) -> bool:
    """用户此刻是否处于「不该被打扰」的状态。

    没有桌面情景（Web 端 / 桌宠端没上报）时返回 False——
    **拿不到信息就不做限制**。反过来的话，Web 端会因为「永远拿不到进程名」
    而永久静音，那等于功能不存在。
    """
    if desktop is None:
        return False
    process = (desktop.foreground_process or "").strip().lower()
    if not process:
        return False
    table = BUSY_PROCESSES if processes is None else processes
    return process in table
