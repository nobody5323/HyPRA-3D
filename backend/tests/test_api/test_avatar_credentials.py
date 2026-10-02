"""数字人凭证（GET|PUT /media/avatar/credentials）的 API 与存储测试。

覆盖的故障模式（都对应真实会踩到的情形）：

- **凭证文件被写坏** → 不能让数字人设置面板起不来：它只是「填过哪些密钥」，
  丢了顶多回到部署配置，不该抛错；
- **半份凭证**（只有 appId 没有 appSecret）→ 必须拦住**且不落盘**，否则下一次
  读到「配了却用不了」的状态，用户还以为保存成功了；
- **两个形态互不干扰**（web = 横屏给 Web 端，pet = 竖屏给桌宠窗）→ 写一个不能
  把另一个覆盖掉，否则桌宠的密钥会顶掉 Web 端的。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
BASE = "/media/avatar/credentials"

UNCONFIGURED = {
    "form": "web",
    "appId": "",
    "appSecret": "",
    "source": "none",
    "configured": False,
}


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path, monkeypatch):
    """每个用例一个干净的凭证文件，并清掉 .env 兜底。

    本机 backend/.env 里可能已经填了真的魔珐密钥（开发者的），
    清空它们才能把「部署配置」与「界面填写」两条路径分开断言。
    """
    path = tmp_path / "avatar-credentials.json"
    monkeypatch.setenv("AVATAR_CREDENTIALS_PATH", str(path))
    monkeypatch.setenv("XMOV_APP_ID", "")
    monkeypatch.setenv("XMOV_SECRET", "")
    yield path


def test_defaults_to_unconfigured():
    assert client.get(BASE).json() == UNCONFIGURED


def test_form_defaults_to_web_and_pet_is_addressable():
    assert client.get(BASE).json()["form"] == "web"
    assert client.get(BASE, params={"form": "pet"}).json()["form"] == "pet"


def test_round_trip_persists_only_that_form(isolated_credentials):
    response = client.put(BASE, json={"form": "pet", "appId": "ak-pet", "appSecret": "sk-pet"})

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "user"
    assert body["configured"] is True
    assert body["appId"] == "ak-pet"

    read_back = client.get(BASE, params={"form": "pet"}).json()
    assert read_back["appSecret"] == "sk-pet"

    # 真落盘（否则重启后端就丢），且**只有该形态**（不多写一个空壳键）
    on_disk = json.loads(isolated_credentials.read_text(encoding="utf-8"))
    assert set(on_disk) == {"pet"}
    assert on_disk["pet"] == {"appId": "ak-pet", "appSecret": "sk-pet"}


def test_forms_do_not_interfere():
    client.put(BASE, json={"form": "web", "appId": "ak-web", "appSecret": "sk-web"})
    client.put(BASE, json={"form": "pet", "appId": "ak-pet", "appSecret": "sk-pet"})

    assert client.get(BASE).json()["appId"] == "ak-web"
    assert client.get(BASE, params={"form": "pet"}).json()["appId"] == "ak-pet"


def test_env_fallback_applies_per_form(monkeypatch):
    monkeypatch.setenv("XMOV_APP_ID", "ak-env")
    monkeypatch.setenv("XMOV_SECRET", "sk-env")

    body = client.get(BASE, params={"form": "pet"}).json()

    assert body["source"] == "env"
    assert body["configured"] is True
    assert body["appId"] == "ak-env"


def test_user_value_overrides_env(monkeypatch):
    monkeypatch.setenv("XMOV_APP_ID", "ak-env")
    monkeypatch.setenv("XMOV_SECRET", "sk-env")

    client.put(BASE, json={"form": "web", "appId": "ak-user", "appSecret": "sk-user"})

    body = client.get(BASE).json()
    assert body["source"] == "user"
    assert body["appId"] == "ak-user"


def test_clearing_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("XMOV_APP_ID", "ak-env")
    monkeypatch.setenv("XMOV_SECRET", "sk-env")
    client.put(BASE, json={"form": "web", "appId": "ak-user", "appSecret": "sk-user"})

    body = client.put(BASE, json={"form": "web", "appId": "", "appSecret": ""}).json()

    assert body["source"] == "env"
    assert body["appId"] == "ak-env"


def test_clearing_without_env_is_unconfigured(isolated_credentials):
    client.put(BASE, json={"form": "web", "appId": "ak-user", "appSecret": "sk-user"})

    body = client.put(BASE, json={"form": "web", "appId": "", "appSecret": ""}).json()

    assert body["source"] == "none"
    assert body["configured"] is False
    # 清除要真的从文件里去键，而不是留一个空壳（否则读回时又要多一条分支去忽略它）
    assert json.loads(isolated_credentials.read_text(encoding="utf-8")) == {}


def test_half_credentials_are_rejected_and_not_persisted(isolated_credentials):
    response = client.put(BASE, json={"form": "web", "appId": "ak-only", "appSecret": ""})

    assert response.status_code == 400
    assert "同时" in response.json()["detail"]
    assert client.get(BASE).json()["source"] == "none"
    # 被拒的写入不能留下痕迹：否则用户以为没保存成功，文件其实已经被改坏
    assert not isolated_credentials.exists()


def test_overlong_value_is_rejected():
    response = client.put(
        BASE, json={"form": "web", "appId": "x" * 513, "appSecret": "y" * 513}
    )

    assert response.status_code == 400
    assert "过长" in response.json()["detail"]


def test_unknown_form_is_rejected_on_both_methods():
    assert client.get(BASE, params={"form": "mobile"}).status_code == 400
    assert client.put(
        BASE, json={"form": "mobile", "appId": "a", "appSecret": "b"}
    ).status_code == 400


def test_form_is_case_insensitive():
    """界面 / 脚本传 `Web` 不该被当成未知形态拒掉。"""
    client.put(BASE, json={"form": "PET", "appId": "ak-pet", "appSecret": "sk-pet"})

    assert client.get(BASE, params={"form": "pet"}).json()["appId"] == "ak-pet"


def test_values_are_trimmed():
    client.put(BASE, json={"form": "web", "appId": "  ak-web  ", "appSecret": "  sk-web  "})

    body = client.get(BASE).json()

    assert body["appId"] == "ak-web"
    assert body["appSecret"] == "sk-web"


def test_corrupted_file_falls_back_to_unconfigured(isolated_credentials):
    isolated_credentials.parent.mkdir(parents=True, exist_ok=True)
    isolated_credentials.write_text("{ 这不是 JSON", encoding="utf-8")

    # 坏文件不能把接口打挂：按「未配置」处理，界面回落到本地渲染器
    assert client.get(BASE).json() == UNCONFIGURED


def test_non_object_file_falls_back_to_unconfigured(isolated_credentials):
    isolated_credentials.parent.mkdir(parents=True, exist_ok=True)
    isolated_credentials.write_text('["unexpected"]', encoding="utf-8")

    assert client.get(BASE).json() == UNCONFIGURED


def test_half_written_entry_on_disk_is_ignored(isolated_credentials):
    """手工改坏文件（只有 appId）时也按未配置处理，不把半份凭证下发给前端。"""
    isolated_credentials.parent.mkdir(parents=True, exist_ok=True)
    isolated_credentials.write_text(
        json.dumps({"web": {"appId": "ak-only"}}), encoding="utf-8"
    )

    body = client.get(BASE).json()

    assert body["source"] == "none"
    assert body["appSecret"] == ""


def test_corrupted_file_does_not_block_writing(isolated_credentials):
    """文件坏了也能自愈：下一次写入重建一份合法 JSON。"""
    isolated_credentials.parent.mkdir(parents=True, exist_ok=True)
    isolated_credentials.write_text("{ 坏掉了", encoding="utf-8")

    client.put(BASE, json={"form": "web", "appId": "ak-web", "appSecret": "sk-web"})

    on_disk = json.loads(isolated_credentials.read_text(encoding="utf-8"))
    assert on_disk == {"web": {"appId": "ak-web", "appSecret": "sk-web"}}
