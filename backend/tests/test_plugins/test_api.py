"""插件管理 API 测试：列表、启停、配置读写、酒馆记忆导入。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.plugins.capabilities import PluginState
from app.plugins.registry import PluginRegistry, get_registry


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# =============================================================
# 列表
# =============================================================


def test_list_plugins_includes_builtin(client: TestClient) -> None:
    body = client.get("/plugins").json()

    ids = [plugin["id"] for plugin in body["plugins"]]
    assert "llm-providers" in ids
    assert "tools-builtin" in ids
    assert body["summary"]["total"] == len(body["plugins"])
    assert "llm" in body["capabilities"]


def test_list_plugins_exposes_capability_index(client: TestClient) -> None:
    body = client.get("/plugins").json()
    capabilities = body["capabilities"]

    # 每个内置能力族都应出现在索引里，且指向具体插件
    for capability in ("llm", "digital_human", "warm_store", "knowledge_store", "session_store"):
        assert capability in capabilities
        assert capabilities[capability]


# =============================================================
# 启停
# =============================================================


def test_toggle_builtin_plugin(client: TestClient) -> None:
    response = client.post("/plugins/tools-builtin/enabled", json={"enabled": False})
    assert response.status_code == 200

    body = response.json()
    assert body["enabled"] is False
    tools_plugin = next(p for p in body["plugins"] if p["id"] == "tools-builtin")
    assert tools_plugin["enabled"] is False
    assert tools_plugin["state"] == "disabled"

    # 还原，避免影响其它用例
    client.post("/plugins/tools-builtin/enabled", json={"enabled": True})


def test_toggle_unknown_plugin_404(client: TestClient) -> None:
    response = client.post("/plugins/not-a-plugin/enabled", json={"enabled": True})
    assert response.status_code == 404
    assert "插件不存在" in response.json()["detail"]


def test_core_plugin_cannot_be_disabled_400(client: TestClient) -> None:
    """core 层拒绝禁用——用真实存在的 core 插件验证（若无则跳过）。"""
    registry = get_registry()
    core_ids = [r.manifest.id for r in registry.all() if r.manifest.is_core]
    if not core_ids:  # 当前收编的 7 项均为 builtin，core 尚未落地
        pytest.skip("尚未注册 core 层插件")

    response = client.post(f"/plugins/{core_ids[0]}/enabled", json={"enabled": False})
    assert response.status_code == 400
    assert "core" in response.json()["detail"]


# =============================================================
# 配置
# =============================================================


def test_get_then_put_plugin_settings(client: TestClient, tmp_path, monkeypatch) -> None:
    """配置读写闭环：写进去的能读回来。"""
    from app.api import plugins as plugins_api

    manager = plugins_api.get_plugin_manager()
    monkeypatch.setattr(manager, "data_dir", tmp_path)

    schema_response = client.get("/plugins/tavern-bridge/settings")
    if schema_response.status_code == 404:
        pytest.skip("tavern-bridge 未安装（plugins/ 目录缺失）")

    body = schema_response.json()
    assert "schema" in body
    assert body["permissions"]["filesystem"]["write"] is False   # 只读声明随配置一起暴露

    put_response = client.put(
        "/plugins/tavern-bridge/settings",
        json={"values": {"tavern_dir": str(tmp_path / "fake-tavern")}},
    )
    assert put_response.status_code == 200

    refreshed = client.get("/plugins/tavern-bridge/settings").json()
    assert refreshed["values"]["tavern_dir"] == str(tmp_path / "fake-tavern")


def test_settings_for_unknown_plugin_404(client: TestClient) -> None:
    assert client.get("/plugins/nope/settings").status_code == 404
    assert client.put("/plugins/nope/settings", json={"values": {}}).status_code == 404


# =============================================================
# 酒馆接入（未启用时给出可读错误）
# =============================================================


def test_tavern_status_reports_not_ready_or_data(client: TestClient) -> None:
    """插件未启用 → 409 且提示怎么做；已启用 → 返回读取统计。"""
    response = client.get("/plugins/tavern-bridge/status")

    if response.status_code == 409:
        assert "启用" in response.json()["detail"]
    else:
        assert response.status_code == 200
        body = response.json()
        assert "available" in body
        assert set(body["available"]) == {"entries", "characters", "sessions"}


def test_tavern_import_requires_companion_id(client: TestClient) -> None:
    """缺少 companion_id → 422（参数校验），不会误写进空命名空间。"""
    assert client.post("/plugins/tavern-bridge/import").status_code == 422
