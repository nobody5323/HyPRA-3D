"""忙碌判定测试（闸门⑥的依据）。

这一层只有一条设计意图要守住：**高置信度才判忙碌**。
误判成忙碌 = 陪伴静音，比多说一句话糟糕得多。
"""

from app.perception.busy import BUSY_PROCESSES, GAME_PROCESSES, MEETING_PROCESSES, is_busy
from app.perception.models import DesktopContext


def test_no_desktop_context_is_not_busy() -> None:
    """拿不到信息就不做限制——否则 Web 端会因「永远拿不到进程名」而永久静音。"""
    assert is_busy(None) is False


def test_empty_process_is_not_busy() -> None:
    assert is_busy(DesktopContext()) is False


def test_meeting_process_is_busy() -> None:
    assert is_busy(DesktopContext(foreground_process="wemeetapp.exe")) is True


def test_game_process_is_busy() -> None:
    assert is_busy(DesktopContext(foreground_process="genshinimpact.exe")) is True


def test_process_match_is_case_insensitive() -> None:
    assert is_busy(DesktopContext(foreground_process="Zoom.exe")) is True


def test_common_work_apps_are_not_busy() -> None:
    """误判成忙碌 = 陪伴静音，比多说一句话糟糕得多。"""
    for process in ("Code.exe", "chrome.exe", "WINWORD.EXE", "explorer.exe", "notepad.exe"):
        assert is_busy(DesktopContext(foreground_process=process)) is False, process


def test_window_title_does_not_affect_busy() -> None:
    """窗口标题是**内容**（可能是一封邮件），不该参与功能判定。"""
    context = DesktopContext(
        foreground_process="Code.exe",
        foreground_title="Zoom 会议纪要 - 文档",
    )
    assert is_busy(context) is False


def test_custom_process_table() -> None:
    table = frozenset({"my-focus-app.exe"})
    assert is_busy(DesktopContext(foreground_process="my-focus-app.exe"), processes=table) is True
    # 自定义表会**替换**默认表（而不是并集）：调用方要的是完全可控
    assert is_busy(DesktopContext(foreground_process="zoom.exe"), processes=table) is False


def test_tables_are_disjoint_and_non_empty() -> None:
    assert MEETING_PROCESSES
    assert GAME_PROCESSES
    assert not (MEETING_PROCESSES & GAME_PROCESSES)
    assert BUSY_PROCESSES >= (MEETING_PROCESSES | GAME_PROCESSES)
