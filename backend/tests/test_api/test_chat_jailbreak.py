"""叙事框架层（jailbreak）的 API 行为测试。

这一层的产品口径是「**默认关闭**、用户知情后自行开启」，所以这里守的第一件事不是
「打开了会怎样」，而是「**不打开时什么都没变**」：

- 出厂状态（不传任何参数）下，`/chat` 的 system prompt 仍以 `[角色人设]` 开头，
  响应里的 `jailbreak` 是空 dict；
- 清单接口 `enabled` 为 false。

第二件事是三种语义不能混：未传 = 跟随部署默认、`"none"` = 强制关闭、
具体 id = 本轮开启。混掉的后果是「用户关不掉」或「用户开不了」，都是静默的。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

LIST = "/chat/jailbreak-presets"
PREFS = "/chat/preferences"


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    """每个用例一个干净的偏好文件（与 test_chat_preferences.py 同一套做法）。

    必须有它：下面几个用例会 PUT 偏好，不隔离就会**改到开发者本机的
    `data/chat-preferences.json`**——跑一次测试就把他自己的开关状态改掉了
    （而且改成一个他不知情的值），这种副作用比测试失败更难发现。
    """
    monkeypatch.setenv("CHAT_PREFERENCES_PATH", str(tmp_path / "chat-preferences.json"))
    yield tmp_path / "chat-preferences.json"


def test_presets_endpoint_is_disabled_by_default() -> None:
    """出厂口径：清单能取到，但 enabled 为 false。"""
    body = client.get(LIST).json()

    assert body["enabled"] is False
    assert body["default_jailbreak_id"] == "immersive-narrative"
    ids = {p["id"] for p in body["presets"]}
    assert {"immersive-narrative", "strict-in-character", "mature-fiction"} <= ids


def test_presets_expose_intensity_and_adult_flag() -> None:
    """清单要带强度与成年标记：界面据此分级展示与二次确认。"""
    presets = {p["id"]: p for p in client.get(LIST).json()["presets"]}

    assert presets["mature-fiction"]["requires_adult"] is True
    assert presets["immersive-narrative"]["requires_adult"] is False
    for preset in presets.values():
        assert preset["intensity_label"]


def test_chat_defaults_to_no_jailbreak_layer() -> None:
    """不传参数时，提示词与响应都体现「这一层不存在」。"""
    body = client.post("/chat", json={"text": "今天有点累"}).json()

    assert body["jailbreak"] == {}
    assert body["system_prompt"].startswith("[角色人设]")
    assert "叙事框架" not in body["system_prompt"]


def test_chat_request_can_enable_one_turn() -> None:
    """请求里显式指定档位 = 本轮开启（无需改任何部署配置）。"""
    body = client.post(
        "/chat",
        json={"text": "陪我演一段", "jailbreak_id": "immersive-narrative"},
    ).json()

    assert body["jailbreak"]["jailbreak_id"] == "immersive-narrative"
    assert body["jailbreak"]["jailbreak_name"] == "沉浸叙事"
    assert body["system_prompt"].startswith("[叙事框架：沉浸叙事]")


def test_chat_request_none_forces_off() -> None:
    """传 "none" = 本轮强制关闭（覆盖用户偏好与部署默认）。"""
    body = client.post(
        "/chat",
        json={"text": "这轮不要", "jailbreak_id": "none"},
    ).json()

    assert body["jailbreak"] == {}
    assert body["system_prompt"].startswith("[角色人设]")


def test_unknown_preset_warns_and_skips() -> None:
    """未知档位不能把请求打挂：跳过该层并给一条可读的告警。"""
    resp = client.post("/chat", json={"text": "你好", "jailbreak_id": "no-such-preset"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["jailbreak"] == {}
    assert any("叙事框架" in w for w in body["warnings"])


def test_preference_round_trip() -> None:
    """档位可以落进用户偏好（控制台 / Web / 桌宠窗共用同一份）。"""
    assert client.put(PREFS, json={"jailbreak_id": "strict-in-character"}).status_code == 200
    assert client.get(PREFS).json()["jailbreak_id"] == "strict-in-character"

    # 显式关掉是"none"，与"未设置"（空串）是两种状态
    client.put(PREFS, json={"jailbreak_id": "none"})
    assert client.get(PREFS).json()["jailbreak_id"] == "none"


def test_partial_update_does_not_wipe_jailbreak_preference() -> None:
    """部分更新不能把没传的字段抹掉（与其它偏好字段同一约定）。"""
    client.put(PREFS, json={"jailbreak_id": "strict-in-character"})
    client.put(PREFS, json={"user_name": "小林"})

    body = client.get(PREFS).json()
    assert body["jailbreak_id"] == "strict-in-character"
    assert body["user_name"] == "小林"
