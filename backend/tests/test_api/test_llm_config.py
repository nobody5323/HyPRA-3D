"""对话模型运行时切换的 API 测试（`/llm/*`）。

不发起任何真实网络请求：切换/测试用的 OpenAI 兼容 provider 只做**构造**（openai SDK
构造不联网），成功路径走 mock provider。
"""

import json

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FAKE_KEY = "sk-test-fake-key-should-never-be-returned"


def config_of(response) -> dict:
    return response.json()["config"]


# --------------------------------------------------------------------------
# 读取配置
# --------------------------------------------------------------------------


def test_default_config_comes_from_env() -> None:
    """没有运行时设置时回落到 .env（测试环境是 mock provider）。"""
    resp = client.get("/llm/config")

    assert resp.status_code == 200
    body = resp.json()
    assert body["config"]["provider"] == "mock"
    assert body["config"]["source"] == "env"
    assert body["config"]["has_api_key"] is False
    # provider 选项由后端给出，前端不再硬编码
    assert {item["value"] for item in body["providers"]} >= {
        "mock",
        "dashscope",
        "siliconflow",
        "openai-compatible",
    }


def test_config_response_never_contains_api_key() -> None:
    """安全红线：任何响应体都不得出现 key 明文（只回 has_api_key）。"""
    client.put(
        "/llm/config",
        json={
            "provider": "openai-compatible",
            "base_url": "https://example.invalid/v1",
            "model": "demo-model",
            "api_key": FAKE_KEY,
        },
    )

    for path in ("/llm/config", "/chat/presets"):
        body = client.get(path).text
        assert FAKE_KEY not in body
        assert "sk-test-fake" not in body

    assert client.get("/llm/config").json()["config"]["has_api_key"] is True


# --------------------------------------------------------------------------
# 切换模型
# --------------------------------------------------------------------------


def test_switch_model_is_effective_and_persisted(tmp_path) -> None:
    """切换后：配置生效（source=runtime）、图被重建、文件落盘（key 在文件里）。"""
    resp = client.put(
        "/llm/config",
        json={
            "provider": "openai-compatible",
            "base_url": "https://example.invalid/v1",
            "model": "demo-model",
            "api_key": FAKE_KEY,
        },
    )
    assert resp.status_code == 200
    config = config_of(resp)
    assert config["provider"] == "openai-compatible"
    assert config["model"] == "demo-model"
    assert config["source"] == "runtime"

    # 预设档的「自动」匹配要跟着换成新模型名
    presets = client.get("/chat/presets").json()
    assert presets["model"] == "demo-model"

    # key 只落本地运行时文件（本测试里指向临时目录）
    runtime_file = tmp_path / "llm_runtime.json"
    assert runtime_file.is_file()
    assert FAKE_KEY in runtime_file.read_text(encoding="utf-8")


def test_switch_model_rejects_missing_credentials() -> None:
    """非 mock provider 缺 key / 模型名时明确报错，不静默降级。"""
    resp = client.put(
        "/llm/config", json={"provider": "dashscope", "model": "qwen-plus", "api_key": ""}
    )
    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]

    resp = client.put(
        "/llm/config", json={"provider": "dashscope", "model": "", "api_key": "sk-x"}
    )
    assert resp.status_code == 400
    assert "模型名" in resp.json()["detail"]


def test_switch_model_rejects_unknown_provider() -> None:
    resp = client.put("/llm/config", json={"provider": "not-a-provider", "model": "x"})
    assert resp.status_code == 400
    assert "未知 provider" in resp.json()["detail"]


def test_openai_compatible_requires_base_url() -> None:
    resp = client.put(
        "/llm/config",
        json={"provider": "openai-compatible", "model": "x", "api_key": "sk-x", "base_url": ""},
    )
    assert resp.status_code == 400
    assert "Base URL" in resp.json()["detail"]


def test_api_key_is_reused_when_omitted() -> None:
    """前端拿不到明文，所以再次切换（只改模型名）时留空 key 应沿用旧值。"""
    client.put(
        "/llm/config",
        json={
            "provider": "openai-compatible",
            "base_url": "https://example.invalid/v1",
            "model": "first-model",
            "api_key": FAKE_KEY,
        },
    )

    resp = client.put(
        "/llm/config",
        json={
            "provider": "openai-compatible",
            "base_url": "https://example.invalid/v1",
            "model": "second-model",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["config"]["model"] == "second-model"
    assert resp.json()["config"]["has_api_key"] is True


def test_persist_false_keeps_changes_in_memory_only(tmp_path) -> None:
    """persist=false：本次运行生效，但不写文件（重启后回到 .env）。"""
    resp = client.put(
        "/llm/config",
        json={
            "provider": "openai-compatible",
            "base_url": "https://example.invalid/v1",
            "model": "temp-model",
            "api_key": FAKE_KEY,
            "persist": False,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["config"]["source"] == "session"
    assert not (tmp_path / "llm_runtime.json").exists()


def test_reset_returns_to_env_config(tmp_path) -> None:
    """DELETE 清除界面设置，回到部署配置（并删除本地文件）。"""
    client.put(
        "/llm/config",
        json={
            "provider": "openai-compatible",
            "base_url": "https://example.invalid/v1",
            "model": "demo-model",
            "api_key": FAKE_KEY,
        },
    )
    assert (tmp_path / "llm_runtime.json").is_file()

    resp = client.delete("/llm/config")

    assert resp.status_code == 200
    assert resp.json()["removed"] is True
    assert resp.json()["config"]["source"] == "env"
    assert resp.json()["config"]["provider"] == "mock"
    assert not (tmp_path / "llm_runtime.json").exists()


# --------------------------------------------------------------------------
# 模型列表 / 连通性测试
# --------------------------------------------------------------------------


def test_list_models_for_mock_provider() -> None:
    """mock 也要能回应（界面据此提示「无需选择模型」）。"""
    resp = client.get("/llm/models")

    assert resp.status_code == 200
    assert resp.json()["models"] == ["mock"]


def test_list_models_without_credentials_is_400() -> None:
    """缺 key 时给出明确原因，而不是 500。"""
    resp = client.get("/llm/models", params={"provider": "siliconflow", "api_key": ""})

    assert resp.status_code == 400
    assert "API Key" in resp.json()["detail"]


def test_test_endpoint_succeeds_with_mock() -> None:
    """连通性测试走真实调用路径（这里用 mock provider，不联网）。"""
    resp = client.post("/llm/config/test", json={"provider": "mock", "model": "mock"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["latency_ms"] >= 0


def test_test_endpoint_reports_config_error_as_result() -> None:
    """配置不全时把原因当作**结果**返回（不是 5xx），界面直接展示。"""
    resp = client.post(
        "/llm/config/test", json={"provider": "dashscope", "model": "qwen-plus", "api_key": ""}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "API Key" in body["error"]


def test_test_endpoint_does_not_change_current_config() -> None:
    """测试连接不得改变当前生效配置（否则用户点一下「测试」就换了模型）。"""
    before = config_of(client.get("/llm/config"))

    client.post(
        "/llm/config/test",
        json={
            "provider": "openai-compatible",
            "base_url": "https://example.invalid/v1",
            "model": "probe-model",
            "api_key": FAKE_KEY,
        },
    )

    after = config_of(client.get("/llm/config"))
    assert after["provider"] == before["provider"]
    assert after["model"] == before["model"]


def test_runtime_file_corruption_falls_back_to_env(tmp_path) -> None:
    """运行时文件损坏时应回落到 .env，而不是让整个服务起不来。"""
    (tmp_path / "llm_runtime.json").write_text("{ not json", encoding="utf-8")

    from app.api import llm as llm_module

    llm_module.reset_llm_config_cache()

    config = config_of(client.get("/llm/config"))
    assert config["source"] == "env"
    assert config["provider"] == "mock"


def test_chat_still_works_after_switch() -> None:
    """切换模型后对话链路仍可用（新 provider 被真正装进图里）。"""
    client.put(
        "/llm/config",
        json={"provider": "mock", "model": "mock", "api_key": ""},
    )
    client.put("/llm/config", json={"provider": "mock", "model": "mock"})

    resp = client.post("/chat", json={"text": "你好"})

    assert resp.status_code == 200
    assert resp.json()["reply"]
    assert "mock" in json.dumps(resp.json()["note"], ensure_ascii=False)
