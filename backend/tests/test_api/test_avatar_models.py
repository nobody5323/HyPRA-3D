"""数字人模型库的 API 与存储层测试。

覆盖的故障模式（都来自真实使用场景）：
- VTS 导出的模型包**没有** Expressions / Motions 引用 → 上传后必须补全，
  否则「模型能显示但一个表情都切不了」；
- 中文文件名（`开心兴奋.exp3.json`）：Windows 打的包用 GBK 存名，
  不修正编码会导致表情文件全部对不上；
- 路径穿越（`../../etc/passwd`、盘符、`%2e%2e`）必须被拦下；
- 静态立绘的**情绪映射只能指向该模型里真实存在的图片**；
- 删除模型要连文件一起清掉，不留孤儿目录。
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.digital_human.model_store import AvatarModelStore, ModelStoreError
from app.main import app

client = TestClient(app)
BASE = "/media/avatar/models"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """每个用例一个独立模型目录（绝不碰真实的 backend/data）。

    必须用环境变量：`get_settings()` 每次调用都新建 `Settings()`，
    patch 某个实例不会影响后续调用（这一点写成 fixture 注释，避免以后又踩）。
    """
    monkeypatch.setenv("AVATAR_MODELS_DIR", str(tmp_path / "avatar_models"))
    yield tmp_path / "avatar_models"


def tiny_png() -> bytes:
    """最小合法 PNG（1x1），避免依赖外部素材。"""
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a4944415478da63000100000500010d0a2db40000000049454e44ae426082"
    )


def make_model_zip(with_expression_links: bool = False) -> bytes:
    """造一个「VTS 风格」的 Live2D 模型包（默认不带 Expressions/Motions 引用）。"""
    model3 = {
        "Version": 3,
        "FileReferences": {
            "Moc": "c_0001.moc3",
            "Textures": ["c_0001.2048/texture_00.png"],
            "Physics": "c_0001.physics3.json",
        },
        "Groups": [
            {"Target": "Parameter", "Name": "EyeBlink", "Ids": ["ParamEyeLOpen", "ParamEyeROpen"]}
        ],
    }
    if with_expression_links:
        model3["FileReferences"]["Expressions"] = [{"Name": "已引用", "File": "已引用.exp3.json"}]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("c_0001.model3.json", json.dumps(model3, ensure_ascii=False))
        handle.writestr("c_0001.moc3", b"MOC3\x04\x00\x00\x00")
        handle.writestr("c_0001.physics3.json", "{}")
        handle.writestr("c_0001.2048/texture_00.png", tiny_png())
        # 中文表情名 + 一个 idle 动作
        handle.writestr("开心兴奋.exp3.json", json.dumps({"Type": "Live2D Expression", "Parameters": []}))
        handle.writestr("生气.exp3.json", json.dumps({"Type": "Live2D Expression", "Parameters": []}))
        handle.writestr("motions/idle.motion3.json", json.dumps({"Meta": {"Loop": True}, "Curves": []}))
        handle.writestr("motions/自拍.motion3.json", json.dumps({"Meta": {}, "Curves": []}))
    return buffer.getvalue()


def upload_live2d(name: str = "测试模型", archive: bytes | None = None) -> dict:
    response = client.post(
        BASE,
        data={"kind": "live2d", "name": name},
        files=[("files", ("model.zip", archive or make_model_zip(), "application/zip"))],
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_images(name: str = "测试立绘", count: int = 3) -> dict:
    files = [("files", (f"pic{index}.png", tiny_png(), "image/png")) for index in range(count)]
    response = client.post(BASE, data={"kind": "images", "name": name}, files=files)
    assert response.status_code == 201, response.text
    return response.json()


# =============================================================
# Live2D
# =============================================================
def test_upload_live2d_fills_missing_expression_and_motion_links():
    """VTS 风格模型：上传后必须补全 Expressions / Motions，否则表情动作全废。"""
    model = upload_live2d()

    assert model["kind"] == "live2d"
    assert model["entry"] == "pet.model3.json"
    # 中文表情名要按名排序后出现在清单里（供前端配置情绪映射）
    assert model["expressions"] == sorted(["开心兴奋", "生气"])

    entry = client.get(f"{BASE}/{model['id']}/files/pet.model3.json")
    assert entry.status_code == 200
    references = entry.json()["FileReferences"]

    assert {item["Name"] for item in references["Expressions"]} == {"开心兴奋", "生气"}
    assert {item["File"] for item in references["Expressions"]} == {
        "开心兴奋.exp3.json",
        "生气.exp3.json",
    }
    # idle 归入官方约定的 Idle 组
    assert set(references["Motions"]) == {"Idle", "自拍"}
    assert references["Motions"]["Idle"][0]["File"] == "motions/idle.motion3.json"
    # LipSync 组会被补上（官方 SDK 的辅助功能会读它）
    names = [group["Name"] for group in entry.json()["Groups"]]
    assert "LipSync" in names


def test_existing_expression_links_are_not_overwritten():
    """模型自带的引用一律不动（标准 Cubism 模型不能被我们改写）。"""
    model = upload_live2d(archive=make_model_zip(with_expression_links=True))

    entry = client.get(f"{BASE}/{model['id']}/files/pet.model3.json").json()
    expressions = entry["FileReferences"]["Expressions"]
    assert len(expressions) == 1
    assert expressions[0]["Name"] == "已引用"


def test_model_files_are_served_including_chinese_names():
    """模型内的文件（含中文名表情）都要能取到，渲染器才加载得动。"""
    model = upload_live2d()

    for path, expected_type in [
        ("c_0001.moc3", None),
        ("c_0001.2048/texture_00.png", "image/png"),
    ]:
        response = client.get(f"{BASE}/{model['id']}/files/{path}")
        assert response.status_code == 200, path
        if expected_type:
            assert response.headers["content-type"].startswith(expected_type)

    # 中文名走 URL 时会被百分号编码，路由必须能接住
    encoded = "开心兴奋.exp3.json"
    response = client.get(f"{BASE}/{model['id']}/files/{encoded}")
    assert response.status_code == 200
    assert response.json()["Type"] == "Live2D Expression"


def test_zip_without_model3_is_rejected_and_nothing_left_behind(isolated_store):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("readme.txt", "not a model")

    response = client.post(
        BASE,
        data={"kind": "live2d", "name": "坏包"},
        files=[("files", ("bad.zip", buffer.getvalue(), "application/zip"))],
    )
    assert response.status_code == 400
    assert "model3.json" in response.json()["detail"]
    # 失败不留半个模型目录
    assert list(isolated_store.glob("m_*")) == []


def test_oldest_layout_wins_when_zip_has_nested_model():
    """压缩包多一层目录（常见于「解压后套一层文件夹」）也要能定位入口。"""
    inner = make_model_zip()
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as handle:
        with zipfile.ZipFile(io.BytesIO(inner)) as source:
            for name in source.namelist():
                handle.writestr(f"CubismModel/{name}", source.read(name))

    model = upload_live2d(archive=outer.getvalue())
    assert model["expressions"] == sorted(["开心兴奋", "生气"])
    entry = client.get(f"{BASE}/{model['id']}/files/CubismModel/pet.model3.json")
    assert entry.status_code == 200


# =============================================================
# 静态立绘：情绪映射由前端逐个指定
# =============================================================
def test_upload_images_keeps_order_and_leaves_mapping_empty():
    model = upload_images(count=3)

    assert model["kind"] == "images"
    assert model["images"] == ["image_00.png", "image_01.png", "image_02.png"]
    # 后端不猜情绪：映射留给前端指定
    assert model["expressionMap"] == {}


def test_expression_map_accepts_only_existing_images_and_known_emotions():
    model = upload_images(count=2)
    url = f"{BASE}/{model['id']}"

    ok = client.patch(url, json={"expressionMap": {"happy": "image_00.png", "sad": ""}})
    assert ok.status_code == 200
    # 空值表示「该情绪不指定」，不应被存进映射
    assert ok.json()["expressionMap"] == {"happy": "image_00.png"}

    unknown_emotion = client.patch(url, json={"expressionMap": {"ecstatic": "image_00.png"}})
    assert unknown_emotion.status_code == 400
    assert "未知情绪" in unknown_emotion.json()["detail"]

    missing_image = client.patch(url, json={"expressionMap": {"happy": "nope.png"}})
    assert missing_image.status_code == 400
    assert "图片不在该模型中" in missing_image.json()["detail"]


def test_expression_map_rejected_for_live2d_model():
    model = upload_live2d()
    response = client.patch(f"{BASE}/{model['id']}", json={"expressionMap": {"happy": "x.png"}})
    assert response.status_code == 400
    assert "静态立绘" in response.json()["detail"]


def test_rejects_unsupported_image_format():
    response = client.post(
        BASE,
        data={"kind": "images", "name": "坏图"},
        files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))],
    )
    assert response.status_code == 400
    assert "不支持的图片格式" in response.json()["detail"]


# =============================================================
# 安全与生命周期
# =============================================================
@pytest.mark.parametrize(
    "attack",
    ["../../../../etc/passwd", "..%2f..%2fsecret", "C:/Windows/win.ini", "a/../../b"],
)
def test_path_traversal_is_blocked(attack):
    model = upload_live2d()
    response = client.get(f"{BASE}/{model['id']}/files/{attack}")
    assert response.status_code == 404


def test_zip_member_traversal_is_blocked():
    """压缩包内藏 `../` 条目时必须整包拒绝，不能落到模型目录之外。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("c_0001.model3.json", json.dumps({"FileReferences": {}}))
        handle.writestr("../escaped.txt", "should not exist")

    response = client.post(
        BASE,
        data={"kind": "live2d", "name": "穿越包"},
        files=[("files", ("evil.zip", buffer.getvalue(), "application/zip"))],
    )
    assert response.status_code == 400
    assert "路径" in response.json()["detail"]


def test_list_get_patch_delete_lifecycle():
    model = upload_images(name="原始名", count=2)
    url = f"{BASE}/{model['id']}"

    listed = client.get(BASE).json()["models"]
    assert [item["id"] for item in listed] == [model["id"]]
    assert client.get(url).json()["name"] == "原始名"

    renamed = client.patch(url, json={"name": "新名字"})
    assert renamed.json()["name"] == "新名字"

    assert client.delete(url).json() == {"deleted": True, "id": model["id"]}
    assert client.get(url).status_code == 404
    assert client.get(BASE).json()["models"] == []
    # 文件也要一起清掉
    assert not (Path(get_settings().avatar_models_dir) / model["id"]).exists()


def test_unknown_model_returns_404():
    assert client.get(f"{BASE}/m_不存在").status_code == 404
    assert client.delete(f"{BASE}/m_不存在").status_code == 404


def test_unknown_kind_returns_422():
    response = client.post(
        BASE,
        data={"kind": "video", "name": "x"},
        files=[("files", ("x.bin", b"x", "application/octet-stream"))],
    )
    assert response.status_code == 422
    assert "未知类型" in response.json()["detail"]


# =============================================================
# 存储层单测（不经 HTTP）
# =============================================================
def test_store_ignores_corrupted_meta(tmp_path):
    """手写坏的 model.json 不应让整个清单接口 500。"""
    store = AvatarModelStore(tmp_path)
    broken = tmp_path / "m_broken"
    broken.mkdir(parents=True)
    (broken / "model.json").write_text("{ not json", encoding="utf-8")

    assert store.list_models() == []
    assert store.get_model("m_broken") is None


def test_safe_relative_path_rejects_tricky_names():
    from app.digital_human.model_store import safe_relative_path

    for bad in ["../x", "..", "a/../../b", "C:/x", "a\\b", "a%2e%2e/b", ""]:
        with pytest.raises(ModelStoreError):
            safe_relative_path(bad)

    assert str(safe_relative_path("motions/idle.motion3.json")) == "motions/idle.motion3.json"


def test_decode_zip_name_restores_gbk_names():
    """中文 Windows 打的包：文件名按 GBK 存，需要还原成可读中文。"""
    from app.digital_human.model_store import decode_zip_name

    info = zipfile.ZipInfo()
    info.flag_bits = 0  # 未声明 UTF-8
    info.filename = "开心兴奋.exp3.json".encode("gbk").decode("cp437")

    assert decode_zip_name(info) == "开心兴奋.exp3.json"
