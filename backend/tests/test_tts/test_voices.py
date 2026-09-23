"""音色表加载测试：合法表、脏数据容错、文件缺失（不抛异常）。"""

import json
import logging

from app.tts.voices import load_voice_config, load_voice_table


def _write(path, payload) -> str:
    """把 payload 写成 JSON 文件（payload 为 str 时按原文写，便于造语法错）。"""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_missing_file_returns_empty_and_stays_quiet(tmp_path, caplog) -> None:
    """未配置（文件不存在）是默认状态，不该刷日志。"""
    with caplog.at_level(logging.WARNING):
        assert load_voice_table(tmp_path / "nope.json") == {}
    assert caplog.records == []


def test_empty_path_returns_empty() -> None:
    assert load_voice_table("") == {}
    assert load_voice_table(None) == {}


def test_valid_table_parses_fields(tmp_path) -> None:
    path = _write(
        tmp_path / "voices.json",
        {
            "gentle": {
                "ref_audio_path": "refs/gentle.wav",
                "prompt_text": "今天也辛苦了。",
                "prompt_lang": "zh",
                "label": "温柔",
            },
            "lively": {"ref_audio_path": "refs/lively.wav"},
        },
    )

    voices = load_voice_table(path)

    assert set(voices) == {"gentle", "lively"}
    assert voices["gentle"].ref_audio_path == "refs/gentle.wav"
    assert voices["gentle"].prompt_text == "今天也辛苦了。"
    assert voices["gentle"].label == "温柔"
    # 缺省值：参考文本可空、语言默认中文、展示名默认空串（前端回落 id）
    assert voices["lively"].prompt_text == ""
    assert voices["lively"].prompt_lang == "zh"
    assert voices["lively"].label == ""


def test_voice_id_is_trimmed(tmp_path) -> None:
    path = _write(tmp_path / "voices.json", {"  gentle  ": {"ref_audio_path": "r.wav"}})
    assert set(load_voice_table(path)) == {"gentle"}


def test_entry_without_reference_audio_is_skipped(tmp_path, caplog) -> None:
    """没有参考音频就没有音色可言 —— 丢掉该条，但保留其余条并记 warning。"""
    path = _write(
        tmp_path / "voices.json",
        {
            "good": {"ref_audio_path": "refs/good.wav"},
            "bad": {"prompt_text": "只有文本没有音频"},
            "blank": {"ref_audio_path": "   "},
        },
    )

    with caplog.at_level(logging.WARNING):
        voices = load_voice_table(path)

    assert set(voices) == {"good"}
    assert len(caplog.records) == 2
    assert all("ref_audio_path" in record.getMessage() for record in caplog.records)


def test_non_object_entry_is_skipped(tmp_path, caplog) -> None:
    path = _write(tmp_path / "voices.json", {"good": {"ref_audio_path": "a.wav"}, "bad": "refs/b.wav"})

    with caplog.at_level(logging.WARNING):
        voices = load_voice_table(path)

    assert set(voices) == {"good"}
    assert "bad" in caplog.records[0].getMessage()


def test_blank_voice_id_is_skipped(tmp_path, caplog) -> None:
    path = _write(tmp_path / "voices.json", {"   ": {"ref_audio_path": "a.wav"}})

    with caplog.at_level(logging.WARNING):
        assert load_voice_table(path) == {}
    assert "空 id" in caplog.records[0].getMessage()


def test_broken_json_returns_empty_and_warns(tmp_path, caplog) -> None:
    path = _write(tmp_path / "voices.json", "{ 这不是 JSON ")

    with caplog.at_level(logging.WARNING):
        assert load_voice_table(path) == {}
    assert "读取失败" in caplog.records[0].getMessage()


def test_top_level_not_object_returns_empty(tmp_path, caplog) -> None:
    path = _write(tmp_path / "voices.json", [{"ref_audio_path": "a.wav"}])

    with caplog.at_level(logging.WARNING):
        assert load_voice_table(path) == {}
    assert "格式" in caplog.records[0].getMessage()


def test_non_utf8_file_returns_empty(tmp_path, caplog) -> None:
    path = tmp_path / "voices.json"
    path.write_bytes(b"\xff\xfe\x00\x01 not utf-8")

    with caplog.at_level(logging.WARNING):
        assert load_voice_table(path) == {}
    assert caplog.records

# ---------- 情绪 → 音色映射（保留键 _emotion_map） ----------


def test_emotion_map_key_is_not_treated_as_voice(tmp_path) -> None:
    """_ 开头的保留键不能被当成音色条目（否则会报"缺少 ref_audio_path"并刷 warning）。"""
    path = _write(
        tmp_path / "voices.json",
        {
            "gentle": {"ref_audio_path": "a.wav"},
            "_emotion_map": {"sad": "gentle"},
        },
    )

    table = load_voice_config(path)

    assert set(table.voices) == {"gentle"}
    assert table.emotion_voices == {"sad": "gentle"}


def test_no_emotion_map_gives_empty_mapping(tmp_path) -> None:
    path = _write(tmp_path / "voices.json", {"gentle": {"ref_audio_path": "a.wav"}})
    assert load_voice_config(path).emotion_voices == {}


def test_emotion_map_normalizes_keys(tmp_path) -> None:
    path = _write(
        tmp_path / "voices.json",
        {"gentle": {"ref_audio_path": "a.wav"}, "_emotion_map": {"  SAD  ": "gentle", "tired": " gentle "}},
    )

    assert load_voice_config(path).emotion_voices == {"sad": "gentle", "tired": "gentle"}


def test_emotion_map_pointing_to_unknown_voice_is_dropped(tmp_path, caplog) -> None:
    """指向不存在音色的映射必须丢弃：留着就是"选了却没声音变化"的坑。"""
    path = _write(
        tmp_path / "voices.json",
        {"gentle": {"ref_audio_path": "a.wav"}, "_emotion_map": {"sad": "nope", "happy": "gentle"}},
    )

    with caplog.at_level(logging.WARNING):
        table = load_voice_config(path)

    assert table.emotion_voices == {"happy": "gentle"}
    assert "不存在的音色" in caplog.records[0].getMessage()
    assert "gentle" in caplog.records[0].getMessage()      # 提示可用音色，便于改配置


def test_emotion_map_not_object_is_ignored(tmp_path, caplog) -> None:
    path = _write(
        tmp_path / "voices.json",
        {"gentle": {"ref_audio_path": "a.wav"}, "_emotion_map": ["sad", "gentle"]},
    )

    with caplog.at_level(logging.WARNING):
        table = load_voice_config(path)

    assert table.emotion_voices == {}
    assert set(table.voices) == {"gentle"}                  # 音色本身不受影响
    assert caplog.records


def test_emotion_map_skips_blank_entries(tmp_path) -> None:
    path = _write(
        tmp_path / "voices.json",
        {"gentle": {"ref_audio_path": "a.wav"}, "_emotion_map": {"": "gentle", "sad": "  "}},
    )
    assert load_voice_config(path).emotion_voices == {}


def test_load_voice_table_still_returns_plain_dict(tmp_path) -> None:
    """向后兼容：只要音色清单的入口行为不变。"""
    path = _write(
        tmp_path / "voices.json",
        {"gentle": {"ref_audio_path": "a.wav"}, "_emotion_map": {"sad": "gentle"}},
    )
    assert set(load_voice_table(path)) == {"gentle"}
