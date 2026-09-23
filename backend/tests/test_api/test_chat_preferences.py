"""对话偏好（GET|PUT /chat/preferences）的 API 与存储测试。

覆盖的故障模式（都对应真实会踩到的情形）：

- **偏好文件被写坏** → 不能让对话起不来：它只是「上次选了哪套提示词」，
  丢了顶多回到部署默认，不该抛错；
- **未知人设 id** → 必须拦住：人设 id 同时是记忆隔离命名空间
  （`companion:{id}`），写错不只是这轮回复不对，而是记忆写进了另一个库；
- **部分更新**不能把没传的字段抹掉（否则「只改文风」会把酒馆预设清空）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BASE = "/chat/preferences"

UNSET = {"persona_id": "", "style_id": "", "preset_id": "", "st_preset_id": ""}


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    """每个用例一个干净的偏好文件（经环境变量注入，与模型库测试同一套做法）。"""
    monkeypatch.setenv("CHAT_PREFERENCES_PATH", str(tmp_path / "chat-preferences.json"))
    yield tmp_path / "chat-preferences.json"


def test_defaults_to_all_unset():
    assert client.get(BASE).json() == UNSET


def test_round_trip_persists_to_disk(isolated_preferences):
    response = client.put(BASE, json={"style_id": "modern-conversational"})

    assert response.status_code == 200
    assert response.json()["style_id"] == "modern-conversational"
    assert client.get(BASE).json()["style_id"] == "modern-conversational"

    # 真落盘：否则重启后端就丢（内存里对、文件里不对最难查）
    on_disk = json.loads(isolated_preferences.read_text(encoding="utf-8"))
    assert on_disk["style_id"] == "modern-conversational"


def test_partial_update_keeps_other_fields():
    client.put(BASE, json={"style_id": "a-style", "st_preset_id": "preset-1"})

    client.put(BASE, json={"style_id": "b-style"})

    body = client.get(BASE).json()
    assert body["style_id"] == "b-style"
    assert body["st_preset_id"] == "preset-1"


def test_empty_string_clears_a_field():
    client.put(BASE, json={"style_id": "x-style"})

    client.put(BASE, json={"style_id": ""})

    assert client.get(BASE).json()["style_id"] == ""


def test_unknown_persona_is_rejected():
    response = client.put(BASE, json={"persona_id": "nobody-here"})

    assert response.status_code == 400
    assert "未知人设" in response.json()["detail"]
    # 被拒的写入不能留下痕迹
    assert client.get(BASE).json()["persona_id"] == ""


def test_known_persona_is_accepted():
    """后端自己声明的缺省人设，不该被自己的校验拦下。"""
    persona_id = client.get("/chat/personas").json()["default_persona_id"]

    response = client.put(BASE, json={"persona_id": persona_id})

    assert response.status_code == 200
    assert response.json()["persona_id"] == persona_id


def test_overlong_value_is_rejected():
    response = client.put(BASE, json={"style_id": "x" * 200})

    assert response.status_code == 400
    assert "过长" in response.json()["detail"]


def test_corrupted_file_falls_back_to_unset(isolated_preferences):
    isolated_preferences.parent.mkdir(parents=True, exist_ok=True)
    isolated_preferences.write_text("{ 这不是 JSON", encoding="utf-8")

    # 坏文件不能把接口打挂：按「未设置」处理，界面回落到部署默认
    assert client.get(BASE).json() == UNSET


def test_non_object_file_falls_back_to_unset(isolated_preferences):
    isolated_preferences.parent.mkdir(parents=True, exist_ok=True)
    isolated_preferences.write_text('["unexpected"]', encoding="utf-8")

    assert client.get(BASE).json() == UNSET
