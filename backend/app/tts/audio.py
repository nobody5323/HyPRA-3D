"""音频时长解析（零依赖，只用标准库）。

为什么需要它：
GPT-SoVITS 的 `/tts` 只返回**音频字节**，不返回时长；而口型时间轴必须按
「实际音频时长」缩放，嘴才能和声音对上。所以要先从容器头把时长读出来。

为什么自己解析而不用 ffmpeg / pydub：
- 评审机器上不一定装了 ffmpeg，多一个外部二进制就多一处现场翻车点；
- WAV 的时长就在头里（byte_rate + data 长度），解析成本极低。

支持 WAV（RIFF）；非 WAV 一律返回 `None`，由调用方按「估算时长」兜底。
"""

# WAV 头里可能出现「长度未知」的写法（流式生成时边写边发）：
# 有的实现写 0，有的写 0xFFFFFFFF。两者都按「文件实际剩余字节」处理。
_UNKNOWN_SIZE_SENTINELS = (0, 0xFFFFFFFF)

# RIFF 头部固定 12 字节；fmt 块最少 16 字节；data 块头 8 字节
_RIFF_HEADER_SIZE = 12
_MIN_WAV_SIZE = _RIFF_HEADER_SIZE + 8 + 16 + 8


def probe_wav_duration_s(data: bytes) -> float | None:
    """从 WAV 字节解析时长（秒）；不是合法 WAV 或信息不足时返回 None。"""
    if len(data) < _MIN_WAV_SIZE:
        return None
    if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None

    byte_rate = 0
    data_size: int | None = None

    pos = _RIFF_HEADER_SIZE
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        chunk_size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        body = pos + 8

        if chunk_id == b"fmt ":
            # fmt 块布局：format(2) channels(2) sample_rate(4) byte_rate(4) ...
            if chunk_size < 16 or body + 12 > len(data):
                return None
            byte_rate = int.from_bytes(data[body + 8 : body + 12], "little")
        elif chunk_id == b"data":
            available = len(data) - body
            # 声明的长度不可信（未知标记 / 超出实际字节）时，用实际剩余字节数
            if chunk_size in _UNKNOWN_SIZE_SENTINELS or chunk_size > available:
                data_size = available
            else:
                data_size = chunk_size
            break  # data 之后就是音频数据本身，无需继续找块

        # 块长度为奇数时 RIFF 要求补一个填充字节
        pos = body + chunk_size + (chunk_size & 1)

    if byte_rate <= 0 or data_size is None or data_size <= 0:
        return None
    return data_size / byte_rate


def probe_audio_duration_s(data: bytes, audio_format: str) -> float | None:
    """按格式解析时长（秒）；不支持的格式返回 None。

    参数:
        data: 音频字节；
        audio_format: 格式名，如 "wav" / ".WAV"（大小写与点号均容错）。
    """
    fmt = (audio_format or "").strip().lower().lstrip(".")
    if fmt == "wav":
        return probe_wav_duration_s(data)
    return None
