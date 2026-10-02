"""自定义文风的接口测试（TestClient，无真实 LLM / embedding）。

覆盖四块：
1. 读取：catalog / 详情（内置文风只读但可见）；
2. 写入：新建 / 更新 / 复制 / 删除，以及内置只读的 403 与参数错误的 400；
3. **新增即生效**：工坊里建好的文风，无需重启就出现在 `GET /chat/styles`
   与下一轮对话里（验证对话图确实被失效重建）——这是「自定义文风」能否用的关键；
4. 删除内置文风只写用户侧隐藏清单，清单里随即消失。
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BUILTIN_STYLE = "brief-direct"


def _create(**overrides) -> dict:
    """建一份最小可用的文风，返回响应 JSON。"""
    payload = {
        "name": "深夜电台",
        "style_prompt": "像深夜电台主持人一样低声说话，语速慢、句子短。",
        "description": "低音量、慢节奏",
        "tags": ["温柔", "慢"],
        "avoid": ["感叹号"],
        "examples": [{"user": "睡不着", "assistant": "那就别急着睡。我在。"}],
        "sampling": {"temperature": 0.75},
        "conflicts_with": ["聒噪"],
    }
    payload.update(overrides)
    resp = client.post("/chat/studio/styles", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- 读取 ----------


def test_catalog_lists_styles_without_body() -> None:
    """catalog 带回文风清单，且清单刻意不带风格指令（详情接口才给）。"""
    body = client.get("/chat/studio/catalog").json()
    styles = {item["id"]: item for item in body["styles"]}
    assert styles[BUILTIN_STYLE]["builtin"] is True
    assert "style_prompt" not in styles[BUILTIN_STYLE]
    assert body["limits"]["style_prompt"] > 0


def test_style_detail_includes_prompt_and_examples() -> None:
    style = client.get(f"/chat/studio/styles/{BUILTIN_STYLE}").json()["style"]
    assert style["builtin"] is True
    assert style["style_prompt"]
    assert style["examples"] and style["examples"][0]["assistant"]


def test_style_detail_404() -> None:
    assert client.get("/chat/studio/styles/user-nope").status_code == 404


# ---------- 写入 ----------


def test_create_style_appears_in_catalog_and_picker() -> None:
    """新建后：工坊清单、对话用的文风选择器、偏好校验都要立刻看得到。"""
    result = _create()
    created = result["style"]
    assert created["id"].startswith("user-")
    assert created["builtin"] is False
    assert created["examples"][0]["user"] == "睡不着"

    # catalog（工坊）里出现
    assert any(item["id"] == created["id"] for item in result["catalog"]["styles"])
    # /chat/styles（对话界面的文风选择器）里也出现——**没有重启后端**
    listed = client.get("/chat/styles").json()["styles"]
    assert any(item["id"] == created["id"] for item in listed)


def test_created_style_is_usable_by_the_chat_graph() -> None:
    """下一轮对话能真的用上这份文风（图被失效重建，而不是「清单里有、对话里没有」）。"""
    created = _create(style_prompt="只回一句话，不超过二十个字。")["style"]
    resp = client.post(
        "/chat",
        json={
            "text": "今天有点累",
            "persona_id": "therapist-elder-sister",
            "style_id": created["id"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 后端回传的「本轮实际生效的文风」必须就是它（而不是回落默认档）
    assert body["style"]["style_id"] == created["id"]
    assert body["style"]["style_name"] == "深夜电台"
    assert not any("未知风格预设" in warning for warning in body["warnings"])


def test_update_style() -> None:
    created = _create()["style"]
    resp = client.put(
        f"/chat/studio/styles/{created['id']}",
        json={"name": "深夜电台 v2", "avoid": [], "sampling": {"temperature": 0.6}},
    )
    assert resp.status_code == 200, resp.text
    style = resp.json()["style"]
    assert style["name"] == "深夜电台 v2"
    assert style["avoid"] == []
    assert style["sampling"]["temperature"] == 0.6
    # 未提交的字段保持不变
    assert style["description"] == "低音量、慢节奏"


def test_update_builtin_style_is_forbidden() -> None:
    resp = client.put(f"/chat/studio/styles/{BUILTIN_STYLE}", json={"name": "改内置"})
    assert resp.status_code == 403
    assert "复制" in resp.json()["detail"]


def test_duplicate_builtin_style_is_editable() -> None:
    resp = client.post(f"/chat/studio/styles/{BUILTIN_STYLE}/duplicate", json={})
    assert resp.status_code == 200, resp.text
    copy = resp.json()["style"]
    assert copy["builtin"] is False
    assert copy["name"] == "简短利落（副本）"
    assert copy["style_prompt"]
    # 副本可以改
    assert client.put(f"/chat/studio/styles/{copy['id']}", json={"name": "我的简短"}).status_code == 200


def test_delete_user_style() -> None:
    created = _create()["style"]
    resp = client.delete(f"/chat/studio/styles/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == created["id"]
    assert client.get(f"/chat/studio/styles/{created['id']}").status_code == 404
    listed = client.get("/chat/styles").json()["styles"]
    assert not any(item["id"] == created["id"] for item in listed)


def test_delete_builtin_style_hides_it_from_picker() -> None:
    """删除内置文风：从清单里消失，但包内资源一字未改。"""
    assert client.delete(f"/chat/studio/styles/{BUILTIN_STYLE}").status_code == 200
    listed = client.get("/chat/styles").json()["styles"]
    assert not any(item["id"] == BUILTIN_STYLE for item in listed)
    assert client.get(f"/chat/studio/styles/{BUILTIN_STYLE}").status_code == 404


# ---------- 校验 ----------


def test_create_rejects_unknown_sampling_key() -> None:
    resp = client.post(
        "/chat/studio/styles",
        json={"name": "拼错", "style_prompt": "低声。", "sampling": {"temperatur": 0.8}},
    )
    assert resp.status_code == 400
    assert "不支持的采样参数" in resp.json()["detail"]


def test_create_rejects_out_of_range_sampling() -> None:
    resp = client.post(
        "/chat/studio/styles",
        json={"name": "越界", "style_prompt": "低声。", "sampling": {"temperature": 80}},
    )
    assert resp.status_code == 400
    assert "应在" in resp.json()["detail"]


def test_create_rejects_empty_style_prompt() -> None:
    resp = client.post("/chat/studio/styles", json={"name": "空的", "style_prompt": "  "})
    assert resp.status_code == 400


def test_create_rejects_half_example() -> None:
    resp = client.post(
        "/chat/studio/styles",
        json={
            "name": "半截示例",
            "style_prompt": "低声。",
            "examples": [{"user": "在吗", "assistant": " "}],
        },
    )
    assert resp.status_code == 400
