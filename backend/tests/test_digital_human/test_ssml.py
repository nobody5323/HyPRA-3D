"""SSML 播报指令测试（魔珐 SDK 主路径）。"""

import pytest
from fastapi.testclient import TestClient

from app.digital_human.ssml import (
    EMOTION_KA_ACTIONS,
    EMOTION_TONES,
    KA_INTENSITY_THRESHOLD,
    build_ka_event,
    build_speak_command,
    build_ssml,
    build_streaming_ssml_chunks,
    resolve_ka_action,
    split_for_streaming,
    strip_ssml,
)
from app.main import app

client = TestClient(app)


# ---------- SSML 构造 ----------


def test_build_ssml_wraps_text() -> None:
    ssml = build_ssml("我在这儿听着呢。")
    assert ssml.startswith("<speak>") and ssml.endswith("</speak>")
    assert "我在这儿听着呢。" in ssml


def test_build_ssml_with_ka_event() -> None:
    ssml = build_ssml("你好呀", ka_action="Hello")
    assert "<type>ka</type>" in ssml
    assert "<action_semantic>Hello</action_semantic>" in ssml
    # KA 指令应在文本之前（先动作后说话，符合官方样例）
    assert ssml.index("ue4event") < ssml.index("你好呀")


def test_build_ka_event_shape() -> None:
    event = build_ka_event("comfort")
    assert event.startswith("<ue4event>") and event.endswith("</ue4event>")
    assert "<data><action_semantic>comfort</action_semantic></data>" in event


def test_xml_special_chars_escaped() -> None:
    """文本中的 XML 特殊字符必须转义，否则 SSML 结构会被破坏。"""
    ssml = build_ssml("5 < 8 & 3 > 1")
    assert "&lt;" in ssml and "&amp;" in ssml and "&gt;" in ssml


def test_strip_ssml_returns_plain_text() -> None:
    ssml = build_ssml("今晚先陪你坐一会儿。", ka_action="comfort")
    assert strip_ssml(ssml) == "今晚先陪你坐一会儿。"


# ---------- 情绪 → KA 动作 ----------


def test_ka_action_mapping_covers_all_emotions() -> None:
    """M4 的 8 类情绪都应有动作映射与语气描述。"""
    from app.tools.emotion import EmotionLabel

    for label in EmotionLabel:
        assert label.value in EMOTION_KA_ACTIONS
        assert label.value in EMOTION_TONES


def test_ka_action_respects_intensity_threshold() -> None:
    assert resolve_ka_action("anxious", 0.9) == "comfort"
    assert resolve_ka_action("anxious", KA_INTENSITY_THRESHOLD - 0.01) == ""
    assert resolve_ka_action(None, 0.9) == ""


def test_high_intensity_adds_action() -> None:
    cmd = build_speak_command("我在听着。", emotion="anxious", intensity=0.8)
    assert cmd.ka_action == "comfort"
    assert "<action_semantic>comfort</action_semantic>" in cmd.ssml
    assert cmd.tone == "柔声、放缓"


def test_low_intensity_omits_action() -> None:
    cmd = build_speak_command("我在听着。", emotion="anxious", intensity=0.2)
    assert cmd.ka_action == ""
    assert "ue4event" not in cmd.ssml


def test_include_ka_can_be_disabled() -> None:
    cmd = build_speak_command("测试", emotion="happy", intensity=0.9, include_ka=False)
    assert cmd.ka_action == ""


def test_unknown_emotion_falls_back_to_neutral() -> None:
    cmd = build_speak_command("测试", emotion="confused", intensity=0.9)
    assert cmd.emotion == "confused"        # 原样保留情绪标签
    assert cmd.ka_action == ""              # 但无动作映射


# ---------- 命令结构 ----------


def test_command_display_text_has_no_tags() -> None:
    cmd = build_speak_command("我会陪着你。", emotion="sad", intensity=0.7)
    assert "<" not in cmd.display_text
    assert cmd.display_text == "我会陪着你。"


def test_command_to_dict_serializable() -> None:
    import json

    cmd = build_speak_command("你好", emotion="happy", intensity=0.6, voice="V1")
    payload = cmd.to_dict()
    json.dumps(payload)
    assert set(payload) >= {"ssml", "display_text", "voice", "emotion", "ka_action", "tone"}


def test_note_explains_tone_not_in_ssml() -> None:
    """语气不写入 SSML 正文（避免被念出来）。"""
    cmd = build_speak_command("你好", emotion="calm", intensity=0.5)
    assert cmd.tone not in cmd.ssml
    assert "语气" in cmd.meta["note"]


# ---------- 流式分段 ----------


def test_split_short_text_single_chunk() -> None:
    assert split_for_streaming("你好呀", max_chars=40) == ["你好呀"]


def test_split_by_sentence() -> None:
    text = "第一句话。第二句话。第三句话。"
    chunks = split_for_streaming(text, max_chars=12)
    assert len(chunks) >= 2
    assert "".join(chunks) == text          # 不丢字


def test_split_hard_cut_long_sentence() -> None:
    text = "啊" * 100
    chunks = split_for_streaming(text, max_chars=30)
    assert all(len(c) <= 30 for c in chunks)
    assert "".join(chunks) == text


def test_split_empty_text() -> None:
    assert split_for_streaming("") == []


# ---------- 流式片段（同一次 <speak> 文档） ----------


def test_streaming_fragments_form_one_speak_document() -> None:
    """片段拼起来必须是**一个合法 SSML 文档**（不是多个根节点）。

    魔珐 SDK 的 `speak(ssml, is_start, is_end)` 中 `is_start/is_end` 是流式
    片段的标志：源码 `sendText` 里 `multi_turn_conversation_id` 只在
    `is_end === true` 时自增，即同一话轮的片段会被服务端**拼起来**当一次播报解析。
    因此「每段各包一层 <speak>」会让拼出来的文档有多个根节点。
    """
    fragments = build_streaming_ssml_chunks(["第一句。", "第二句。", "第三句。"])
    assert len(fragments) == 3

    merged = "".join(fragments)
    assert merged == "<speak>第一句。第二句。第三句。</speak>"


def test_streaming_fragments_ka_only_in_first() -> None:
    """KA 动作只放首段：一句内连续触发多次动作指令会让数字人反复抖动。"""
    fragments = build_streaming_ssml_chunks(["甲", "乙"], ka_action="comfort")
    assert fragments[0].startswith("<speak><ue4event>")
    assert "<action_semantic>comfort</action_semantic>" in fragments[0]
    assert "<ue4event>" not in fragments[1]
    assert "".join(fragments).endswith("乙</speak>")


def test_streaming_fragments_single_chunk_is_complete_document() -> None:
    """只有一段时，它自身就是完整文档（首段与末段重合，两个标记都要落到它上面）。"""
    assert build_streaming_ssml_chunks(["只有一句。"]) == ["<speak>只有一句。</speak>"]


def test_streaming_fragments_escape_xml_and_drop_blank() -> None:
    """文本转义由后端负责；纯空白段被丢弃（否则 KA 动作可能落进空段）。"""
    fragments = build_streaming_ssml_chunks(["a < b", "   ", "c & d"], ka_action="idle")
    assert len(fragments) == 2
    assert "a &lt; b" in fragments[0]
    assert "c &amp; d" in fragments[1]
    assert all("<ue4event>" not in part for part in fragments[1:])


def test_streaming_fragments_empty_input() -> None:
    assert build_streaming_ssml_chunks([]) == []
    assert build_streaming_ssml_chunks(["   ", "\n"]) == []


# ---------- API：/media/speak ----------


def test_speak_endpoint_returns_ssml() -> None:
    resp = client.post(
        "/media/speak",
        json={"text": "我在这儿听着呢，慢慢说。", "emotion": "anxious", "intensity": 0.8},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ssml"].startswith("<speak>")
    assert body["ka_action"] == "comfort"
    assert body["display_text"] == "我在这儿听着呢，慢慢说。"
    assert body["tone"] == "柔声、放缓"
    assert body["voice"]                      # 缺省音色已填充


def test_speak_endpoint_streaming_chunks() -> None:
    resp = client.post(
        "/media/speak",
        json={
            "text": "第一句。第二句。第三句。第四句。",
            "emotion": "calm",
            "streaming": True,
            "max_chars": 10,
        },
    )
    body = resp.json()
    assert body["chunks"]
    assert "".join(body["chunks"]) == "第一句。第二句。第三句。第四句。"

    # 流式片段是**同一个 <speak> 文档**的若干部分，而不是各自独立的文档：
    # 前端按「首段 is_start / 末段 is_end」把它们作为**同一次播报**喂给 SDK，
    # 服务端会把同一话轮的片段拼起来解析——若每段各包一层 <speak>，
    # 拼出来就是多个根节点（非法 XML）。
    assert len(body["ssml_chunks"]) == len(body["chunks"])
    merged = "".join(body["ssml_chunks"])
    assert merged.startswith("<speak>")
    assert merged.endswith("</speak>")
    assert merged.count("<speak>") == 1
    assert merged.count("</speak>") == 1
    # 首段之外不得再出现 <speak> 开标签
    assert all("<speak>" not in part for part in body["ssml_chunks"][1:])
    # KA 动作只放首段（动作服务于整段表达，避免一句内反复触发）
    if body["ka_action"]:
        assert "<ue4event>" in body["ssml_chunks"][0]
        assert all("<ue4event>" not in part for part in body["ssml_chunks"][1:])


def test_speak_endpoint_streaming_drops_blank_chunks() -> None:
    """纯空白分段必须被滤掉。

    分句正则会把换行单独切成一段；空段的 SSML 会退化为 `<speak></speak>`，
    若它撞上首段，KA 动作会被包进空段从而完全丢失。
    """
    resp = client.post(
        "/media/speak",
        json={
            "text": "甲" * 40 + "\n" + "乙" * 50,
            "emotion": "anxious",
            "intensity": 0.9,
            "streaming": True,
            "max_chars": 40,
        },
    )
    body = resp.json()
    assert body["chunks"]
    assert all(chunk.strip() for chunk in body["chunks"])
    assert len(body["ssml_chunks"]) == len(body["chunks"])
    # 首段非空 → KA 动作不会落在空段上
    assert body["ka_action"]
    assert "<ue4event>" in body["ssml_chunks"][0]


def test_speak_endpoint_requires_text() -> None:
    assert client.post("/media/speak", json={"text": ""}).status_code == 422


def test_chat_response_includes_speak_command() -> None:
    """chat 应联动产出播报指令（回复 + 情绪 → SSML）。"""
    body = client.post("/chat", json={"text": "我最近总是失眠，压力好大"}).json()
    assert body["speak"]
    assert body["speak"]["ssml"].startswith("<speak>")
    assert body["speak"]["display_text"] == body["reply"]
    assert body["speak"]["emotion"] == "anxious"
    assert body["speak"]["ka_action"] == "comfort"      # 强度 0.7 达阈值
