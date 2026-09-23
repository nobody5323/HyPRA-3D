"""CORS 配置测试。

背景：前端（Next.js :3000）与后端（:8000）不同源，若未配置 CORS，
浏览器会拦截请求，表现为前端一直显示「后端未连接」（curl 不受影响，故容易漏测）。
"""

import re

from fastapi.testclient import TestClient

from app.config import Settings, cors_origin_list
from app.main import app

client = TestClient(app)


def test_cors_headers_on_actual_request() -> None:
    """带 Origin 的请求应返回允许跨域的响应头。"""
    resp = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_preflight_options() -> None:
    """预检请求（OPTIONS）应通过，且允许 POST 与 Content-Type。"""
    resp = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "POST" in resp.headers.get("access-control-allow-methods", "")


def test_cors_unknown_origin_not_allowed() -> None:
    """未在允许列表中的来源不应获得允许头（防止任意站点调用）。"""
    resp = client.get("/health", headers={"Origin": "http://evil.example.com"})
    assert resp.headers.get("access-control-allow-origin") != "http://evil.example.com"


def test_cors_allows_loopback_any_port() -> None:
    """回环地址的任意端口都应被允许。

    桌面端（Electron）内置静态服务的端口从 34567 起找可用端口，Web 端也可能改端口；
    端口写死会让它们被 CORS 拦掉，表现为「后端未连接」而 curl 正常。
    """
    for origin in ("http://127.0.0.1:34567", "http://localhost:34567", "http://127.0.0.1"):
        resp = client.get("/health", headers={"Origin": origin})
        assert resp.headers.get("access-control-allow-origin") == origin


def test_cors_preflight_from_desktop_origin() -> None:
    """桌面端的对话请求是 application/json，会先发 OPTIONS 预检，必须通过。"""
    resp = client.options(
        "/chat",
        headers={
            "Origin": "http://127.0.0.1:34567",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:34567"


def test_cors_regex_does_not_allow_remote_hosts() -> None:
    """正则只放行回环地址，局域网地址不因此获得允许头。"""
    resp = client.get("/health", headers={"Origin": "http://192.168.1.20:34567"})
    assert resp.headers.get("access-control-allow-origin") != "http://192.168.1.20:34567"


# ---------- 配置解析 ----------


def test_cors_origin_list_parses_csv() -> None:
    settings = Settings(_env_file=None, cors_origins="http://a.com, http://b.com")
    assert cors_origin_list(settings) == ["http://a.com", "http://b.com"]


def test_cors_origin_list_supports_wildcard() -> None:
    settings = Settings(_env_file=None, cors_origins="*")
    assert cors_origin_list(settings) == ["*"]


def test_cors_origin_list_ignores_empty_entries() -> None:
    settings = Settings(_env_file=None, cors_origins="http://a.com,,  ,")
    assert cors_origin_list(settings) == ["http://a.com"]


def test_default_origins_cover_local_frontend() -> None:
    """默认配置应覆盖本地开发的前端地址（否则开箱即用会失败）。"""
    origins = cors_origin_list(Settings(_env_file=None))
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins


def test_default_regex_covers_loopback_any_port() -> None:
    """默认正则应匹配回环地址的任意端口，且不匹配外部主机。"""
    pattern = Settings(_env_file=None).cors_origin_regex
    assert re.match(pattern, "http://127.0.0.1:34567")
    assert re.match(pattern, "http://localhost:5173")
    assert not re.match(pattern, "http://evil.example.com")
    assert not re.match(pattern, "http://127.0.0.1.evil.com")


def test_regex_can_be_disabled() -> None:
    """置空即关闭正则白名单（只用固定列表）。"""
    assert Settings(_env_file=None, cors_origin_regex="").cors_origin_regex == ""
