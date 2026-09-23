"""音频时长解析测试：用标准库构造内存 WAV，不依赖真实音频文件。"""

import io
import wave

from app.tts.audio import probe_audio_duration_s, probe_wav_duration_s

SAMPLE_RATE = 16000
SAMPWIDTH = 2
CHANNELS = 1


def _make_wav(seconds: float) -> bytes:
    """构造一段静音 WAV（PCM 16bit 单声道）。"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(CHANNELS)
        writer.setsampwidth(SAMPWIDTH)
        writer.setframerate(SAMPLE_RATE)
        frames = int(seconds * SAMPLE_RATE)
        writer.writeframes(b"\x00" * frames * SAMPWIDTH * CHANNELS)
    return buffer.getvalue()


def _data_field_offset(data: bytes) -> int:
    """返回 data 块长度字段的起始偏移。"""
    index = data.index(b"data")
    return index + 4


def _patch_data_size(data: bytes, size: int) -> bytes:
    offset = _data_field_offset(data)
    patched = bytearray(data)
    patched[offset : offset + 4] = size.to_bytes(4, "little")
    return bytes(patched)


def _patch_byte_rate(data: bytes, byte_rate: int) -> bytes:
    """把 fmt 块里的 byte_rate 改成指定值（fmt 块布局固定，byte_rate 在 body+8）。"""
    body = data.index(b"fmt ") + 8
    patched = bytearray(data)
    patched[body + 8 : body + 12] = byte_rate.to_bytes(4, "little")
    return bytes(patched)


def _insert_chunk(data: bytes, chunk_id: bytes, payload: bytes) -> bytes:
    """在 data 块之前插入一个自定义块（含奇数长度时的填充字节），并同步 RIFF 总长。"""
    insert_at = data.index(b"data")
    chunk = chunk_id + len(payload).to_bytes(4, "little") + payload
    if len(payload) % 2:            # 奇数长度需补一个填充字节
        chunk += b"\x00"
    merged = bytearray(data[:insert_at] + chunk + data[insert_at:])
    merged[4:8] = (len(merged) - 8).to_bytes(4, "little")
    return bytes(merged)


# ---------- WAV 解析 ----------


def test_probe_returns_duration_of_one_second() -> None:
    assert probe_wav_duration_s(_make_wav(1.0)) == 1.0


def test_probe_returns_fractional_duration() -> None:
    assert probe_wav_duration_s(_make_wav(2.5)) == 2.5


def test_probe_handles_patched_byte_rate_zero() -> None:
    """byte_rate 为 0 时无法计算时长，必须返回 None（而不是除零崩溃）。"""
    assert probe_wav_duration_s(_patch_byte_rate(_make_wav(1.0), 0)) is None


def test_probe_tolerates_unknown_data_size_sentinel_max() -> None:
    """流式写入的 WAV 会把 data 长度写成 0xFFFFFFFF：按实际剩余字节算。"""
    data = _patch_data_size(_make_wav(2.5), 0xFFFFFFFF)
    assert probe_wav_duration_s(data) == 2.5


def test_probe_tolerates_unknown_data_size_sentinel_zero() -> None:
    assert probe_wav_duration_s(_patch_data_size(_make_wav(2.5), 0)) == 2.5


def test_probe_tolerates_declared_size_beyond_file() -> None:
    """声明长度大于实际字节（截断音频）时，以实际字节为准。"""
    data = _patch_data_size(_make_wav(2.5), 999_999_999)
    assert probe_wav_duration_s(data) == 2.5


def test_probe_skips_intermediate_chunks() -> None:
    """fmt 与 data 之间还可以有别的块（含奇数长度填充），必须能继续走到 data。"""
    data = _insert_chunk(_make_wav(1.0), b"LIST", b"INFOodd")  # 7 字节 → 需填充
    assert probe_wav_duration_s(data) == 1.0


def test_probe_rejects_non_riff() -> None:
    assert probe_wav_duration_s(b"not a wav file at all, definitely long enough......") is None


def test_probe_rejects_truncated_header() -> None:
    assert probe_wav_duration_s(b"RIFF\x00\x00\x00\x00WAVE") is None


def test_probe_rejects_empty() -> None:
    assert probe_wav_duration_s(b"") is None


# ---------- 按格式分发 ----------


def test_probe_audio_duration_dispatches_wav_with_format_noise() -> None:
    """格式名带点号 / 大小写混合也要能识别。"""
    assert probe_audio_duration_s(_make_wav(1.0), ".WAV") == 1.0
    assert probe_audio_duration_s(_make_wav(1.0), " wav ") == 1.0


def test_probe_audio_duration_unknown_format_returns_none() -> None:
    """非 WAV（ogg/aac/raw）没有轻量解析方案 → None，由调用方按估算兜底。"""
    for fmt in ("ogg", "aac", "raw", "mp3", ""):
        assert probe_audio_duration_s(b"\x00\x01\x02" * 100, fmt) is None
