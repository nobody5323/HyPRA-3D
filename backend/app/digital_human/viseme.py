"""口型时间轴生成：文本 / 字级时间戳 → viseme 序列。

两种数据源：
1. **精确**：魔珐 TTS 的 CHAR_TIME_MAP（每字起止秒），时间轴完全对齐语音；
2. **估算**：无音频时按字符数与语速估算（本地降级路径）。

关于口型分类：中文口型主要由韵母决定。当前实现不引入拼音库，
采用「确定性启发式」——同一字必得同一口型（可测试），相邻字避免重复，
标点处闭嘴停顿。接入拼音库后可升级为韵母级精确映射（见 classify_char 注释）。
"""

import hashlib

from app.digital_human.models import Viseme, VisemeFrame

# 标点与空白 → 闭嘴停顿
PAUSE_CHARS = set("，。！？；：、…—,.;:!?()（） \n\t\"'“”‘’《》")

# 元音口型集合（启发式轮换用）
_VOWELS = [Viseme.A, Viseme.I, Viseme.U, Viseme.E, Viseme.O]

# 声母级近似（可确定判断的少数情形）
_M_ONSET = set("mbp")          # 闭唇音
_F_ONSET = set("fv")           # 唇齿音
_S_ONSET = set("szcx")         # 齿音

# 默认语速：每个中文字约 180ms（约 5.5 字/秒，接近自然语速）
DEFAULT_CHAR_MS = 180
# 标点停顿
PAUSE_MS = 220


def classify_char(char: str, index: int = 0, prev: Viseme | None = None) -> Viseme:
    """单字 → 口型（确定性启发式）。

    TODO（可选增强）：接入拼音库（如 pypinyin）后改为
    「声母 → 辅音口型，韵母 → 元音口型」，可显著提升口型精确度。
    """
    if not char or char in PAUSE_CHARS:
        return Viseme.SIL
    if char.isascii() and char.isalpha():
        # 拉丁字母按近似元音分布
        return _VOWELS[ord(char.lower()) % len(_VOWELS)]

    # 确定性：同一字必得同一口型（hash 取模）
    digest = hashlib.md5(char.encode("utf-8")).digest()
    candidate = _VOWELS[int.from_bytes(digest[:2], "big") % len(_VOWELS)]
    # 相邻不重复，让嘴型有变化
    if prev is not None and candidate == prev:
        candidate = _VOWELS[(_VOWELS.index(candidate) + 1) % len(_VOWELS)]
    return candidate


def build_viseme_track_from_char_times(
    char_times: list[tuple[str, float, float]],
) -> list[VisemeFrame]:
    """用魔珐 TTS 的字级时间戳（秒）构建精确口型时间轴。"""
    frames: list[VisemeFrame] = []
    prev: Viseme | None = None
    for index, (char, start_s, end_s) in enumerate(char_times):
        viseme = classify_char(char, index, prev)
        if viseme != Viseme.SIL:
            prev = viseme
        frames.append(
            VisemeFrame(
                start_ms=int(round(start_s * 1000)),
                end_ms=int(round(end_s * 1000)),
                viseme=viseme,
                char=char,
            )
        )
    return frames


def build_viseme_track_estimated(
    text: str,
    *,
    char_ms: int = DEFAULT_CHAR_MS,
    pause_ms: int = PAUSE_MS,
) -> list[VisemeFrame]:
    """无音频时间戳时，按字符数估算口型时间轴（本地降级路径）。"""
    frames: list[VisemeFrame] = []
    cursor = 0
    prev: Viseme | None = None
    for index, char in enumerate(text):
        viseme = classify_char(char, index, prev)
        if viseme != Viseme.SIL:
            prev = viseme
        span = pause_ms if viseme == Viseme.SIL else char_ms
        frames.append(
            VisemeFrame(start_ms=cursor, end_ms=cursor + span, viseme=viseme, char=char)
        )
        cursor += span
    return frames


def track_duration_ms(frames: list[VisemeFrame]) -> int:
    """时间轴总时长（无帧时为 0）。"""
    return max((f.end_ms for f in frames), default=0)


def scale_track_ms(frames: list[VisemeFrame], target_ms: int) -> list[VisemeFrame]:
    """把口型时间轴**等比缩放**到目标总时长。

    为什么需要：估算轨道是按固定语速（`DEFAULT_CHAR_MS`）写死的，而真实语音长
    短由 TTS 决定。带字级时间戳的 TTS（魔珐）不需要这一步；只返回音频的 TTS
    （GPT-SoVITS）必须把整条轨道拉伸/压缩到实际音频长度，嘴才不会越说越飘。

    一致性保证：所有边界乘以同一个系数，因此
    「相邻帧首尾相接、单调不重叠」在缩放后依然成立；取整后用 `max` 兜底，
    避免极短帧被压成 `end < start`。

    参数:
        frames: 原时间轴；
        target_ms: 目标总时长（毫秒）；<= 0 或原时长为 0 时原样返回副本。
    """
    total_ms = track_duration_ms(frames)
    if not frames or total_ms <= 0 or target_ms <= 0:
        return list(frames)

    factor = target_ms / total_ms
    scaled: list[VisemeFrame] = []
    for frame in frames:
        start = int(round(frame.start_ms * factor))
        end = int(round(frame.end_ms * factor))
        scaled.append(
            VisemeFrame(
                start_ms=start,
                end_ms=max(start, end),
                viseme=frame.viseme,
                char=frame.char,
            )
        )
    return scaled
