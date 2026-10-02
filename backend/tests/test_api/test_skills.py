"""技能 API 测试（`/skills/*`）。

技能与插件是两套东西——插件是「代码能力可插拔」（进程级），skill 是
「方法论可插拔」（提示词级），因此不挂在 `/plugins` 下（见 `app/api/skills.py`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.skills.registry import SkillRegistry, get_skill_registry, set_skill_registry


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def skills(tmp_path: Path, monkeypatch):
    """把全局技能库换成临时目录里的两个技能，用完还原。

    同时把技能目录配置指向 tmp_path：`POST /skills/reload` 是按**配置**重新扫描的，
    不改配置的话那一步会把技能库换成真实的 `backend/skills/`。
    """
    root = tmp_path / "skills"
    for skill_id, name in (("alpha", "甲技能"), ("beta", "乙技能")):
        directory = root / skill_id
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: 说明 {skill_id}\n"
            f"when_to_use: 场景 {skill_id}\n---\n\n正文 {skill_id}\n",
            encoding="utf-8",
        )

    monkeypatch.setenv("SKILLS_DIR", str(root))
    monkeypatch.setenv("USER_SKILLS_DIR", str(tmp_path / "user"))
    monkeypatch.setenv("SKILLS_STATE_FILE", str(tmp_path / "skills.json"))

    registry = SkillRegistry(state_path=tmp_path / "skills.json")
    # user_dir 必须一起给：删除自建技能要知道删哪儿（registry 记不住就拒删）
    registry.load(builtin_dir=root, user_dir=tmp_path / "user")
    original = get_skill_registry()
    set_skill_registry(registry)
    try:
        yield registry
    finally:
        set_skill_registry(original)


def test_list_skills(client: TestClient, skills: SkillRegistry) -> None:
    body = client.get("/skills").json()

    assert body["summary"] == {"total": 2, "enabled": 2, "hidden": 0}
    ids = [item["id"] for item in body["skills"]]
    assert ids == ["alpha", "beta"]
    # 列表不含正文（可能上万 token）
    assert "body" not in body["skills"][0]
    assert body["skills"][0]["body_chars"] > 0


def test_get_skill_detail_includes_body(client: TestClient, skills: SkillRegistry) -> None:
    body = client.get("/skills/alpha").json()

    assert body["id"] == "alpha"
    assert body["name"] == "甲技能"
    assert body["when_to_use"] == "场景 alpha"
    assert "正文 alpha" in body["body"]
    assert body["enabled"] is True


def test_get_unknown_skill_404(client: TestClient, skills: SkillRegistry) -> None:
    response = client.get("/skills/nope")

    assert response.status_code == 404
    assert "技能不存在" in response.json()["detail"]


def test_toggle_skill_persists(client: TestClient, skills: SkillRegistry) -> None:
    response = client.post("/skills/alpha/enabled", json={"enabled": False})
    assert response.status_code == 200

    body = response.json()
    assert body["enabled"] is False
    alpha = next(item for item in body["skills"] if item["id"] == "alpha")
    assert alpha["enabled"] is False

    state = json.loads((skills.state_path).read_text(encoding="utf-8"))
    assert state == {"disabled": ["alpha"]}


def test_toggle_unknown_skill_404(client: TestClient, skills: SkillRegistry) -> None:
    response = client.post("/skills/nope/enabled", json={"enabled": False})

    assert response.status_code == 404
    assert "技能不存在" in response.json()["detail"]


def test_reload_rescans_directory(client: TestClient, skills: SkillRegistry, tmp_path: Path) -> None:
    """用户新写一个 SKILL.md 后不该被迫重启后端。"""
    before = client.get("/skills").json()["summary"]["total"]

    new_dir = tmp_path / "skills" / "gamma"
    new_dir.mkdir()
    (new_dir / "SKILL.md").write_text(
        "---\nname: 丙技能\n---\n\n正文 gamma\n", encoding="utf-8"
    )

    response = client.post("/skills/reload")
    assert response.status_code == 200

    body = response.json()
    assert body["summary"]["total"] == before + 1
    assert "gamma" in [item["id"] for item in body["skills"]]


# =============================================================
# AI 编写技能与新建技能（AGENTS.md §9.12）
# =============================================================

_SKILL_BODY = (
    "# 降火\n\n## 步骤\n1. 先复述对方原话，不评判、不解释。\n"
    "2. 确认对方在看这件事上最痛的那一点。\n3. 等对方语速慢下来，再谈事实与选择。\n"
    "## 话术\n「听起来你今天被伤得挺重的。」\n## 红线\n不诊断、不说教、不替对方决定。\n"
)
_SKILL_JSON = (
    '{"id": "Calm Down", "name": "降火", "description": "先把火气降下来",'
    ' "when_to_use": "用户明显在气头上、开始指责他人时",'
    f' "body": {json.dumps(_SKILL_BODY, ensure_ascii=False)}}}'
)


def test_ai_draft_returns_editable_draft(
    client: TestClient, skills: SkillRegistry, scripted_llm, tmp_path: Path
) -> None:
    """草稿只返回内容，**不落盘**——用户看过改过才保存。"""
    scripted_llm([_SKILL_JSON])

    body = client.post("/skills/ai-draft", json={"brief": "让火气降下来"}).json()

    assert body["draft"]["id"] == "calm-down"  # id 被规范化了
    assert body["draft"]["name"] == "降火"
    assert body["model"] == "scripted-model"
    assert not (tmp_path / "user" / "calm-down").exists()


def test_ai_draft_rejects_mock_model(client: TestClient, skills: SkillRegistry) -> None:
    """本地占位模型（mock，conftest 默认）直接拒绝，并说清该怎么办。"""
    response = client.post("/skills/ai-draft", json={"brief": "随便写一个"})

    assert response.status_code == 400
    assert "占位模型" in response.json()["detail"]


def test_create_skill_writes_user_dir_and_reloads(
    client: TestClient, skills: SkillRegistry, tmp_path: Path
) -> None:
    """新建技能：写进用户目录，并在同一次请求里重扫技能库。"""
    response = client.post(
        "/skills",
        json={
            "id": "calm-down",
            "name": "降火",
            "description": "先把火气降下来",
            "when_to_use": "用户在气头上时",
            "body": "# 降火\n\n先复述，再谈事。\n",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert (tmp_path / "user" / "calm-down" / "SKILL.md").is_file()
    assert body["id"] == "calm-down"
    # 清单里立刻看得到（免重启）
    assert "calm-down" in [item["id"] for item in body["skills"]]
    assert client.get("/skills/calm-down").json()["body"].startswith("# 降火")


def test_create_skill_refuses_duplicate(
    client: TestClient, skills: SkillRegistry
) -> None:
    payload = {"id": "calm-down", "name": "降火", "body": "# 降火\n\n内容。\n"}
    assert client.post("/skills", json=payload).status_code == 200

    second = client.post("/skills", json=payload)

    assert second.status_code == 400
    assert "已存在" in second.json()["detail"]


def test_create_skill_refuses_bad_id(client: TestClient, skills: SkillRegistry) -> None:
    response = client.post("/skills", json={"id": "Bad Id", "name": "x", "body": "正文"})

    assert response.status_code == 400


def test_create_skill_refuses_builtin_id(
    client: TestClient, skills: SkillRegistry, tmp_path: Path
) -> None:
    """与**内置**技能同名会静默失效（装载时内置优先）→ 必须拒写。"""
    payload = {"id": "alpha", "name": "冒牌货", "body": "# 冒牌\n\n这套说明永远不会生效。\n"}

    response = client.post("/skills", json=payload)

    assert response.status_code == 400
    assert "内置" in response.json()["detail"]
    # 磁盘上不该多出用户版本（否则能力中心会一直列着一份读不到的东西）
    assert not (tmp_path / "user" / "alpha").exists()


def test_health_exposes_skills(client: TestClient, skills: SkillRegistry) -> None:
    """能力中心的面板从 /health 拿列表，不必额外发一次请求。"""
    body = client.get("/health").json()

    assert isinstance(body["skills"], list)
    assert {item["id"] for item in body["skills"]} == {"alpha", "beta"}


def test_health_keeps_hidden_entry_with_flag(client: TestClient, skills: SkillRegistry) -> None:
    """面板的清单来自 /health：隐藏项必须带标记地出现，否则「恢复」无处可点。"""
    client.delete("/skills/alpha")

    body = client.get("/health").json()
    by_id = {item["id"]: item for item in body["skills"]}

    assert by_id["alpha"]["deleted"] is True


# =============================================================
# 删除与恢复（AGENTS.md §9.6）
# =============================================================


def test_delete_user_skill_removes_file(
    client: TestClient, skills: SkillRegistry, tmp_path: Path
) -> None:
    """自建技能删除 = 删掉用户自己的文件，并在同一次请求里给出刷新后的清单。"""
    client.post(
        "/skills",
        json={"id": "calm-down", "name": "降火", "body": "# 降火\n\n先复述，再谈事。\n"},
    )
    assert (tmp_path / "user" / "calm-down" / "SKILL.md").is_file()

    response = client.delete("/skills/calm-down")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == "calm-down"
    assert body["source"] == "user"
    assert body["hidden"] is False
    assert "calm-down" in (body["path"] or "")
    assert not (tmp_path / "user" / "calm-down").exists()
    assert "calm-down" not in [item["id"] for item in body["skills"]]
    assert client.get("/skills/calm-down").status_code == 404


def test_delete_builtin_skill_hides_file_and_restores(
    client: TestClient, skills: SkillRegistry, tmp_path: Path
) -> None:
    """内置技能删除 = 写隐藏清单：包内文件一字未改，随时能恢复。"""
    builtin_file = tmp_path / "skills" / "alpha" / "SKILL.md"

    response = client.delete("/skills/alpha")

    assert response.status_code == 200
    body = response.json()
    assert body["hidden"] is True
    assert body["path"] is None
    assert builtin_file.is_file()  # 随包分发的文件没被动过
    assert body["summary"] == {"total": 1, "enabled": 1, "hidden": 1}
    entry = next(item for item in body["skills"] if item["id"] == "alpha")
    assert entry["deleted"] is True
    assert client.get("/skills/alpha").status_code == 404

    restored = client.post("/skills/alpha/restore")

    assert restored.status_code == 200
    assert restored.json()["restored"] == "alpha"
    assert restored.json()["summary"] == {"total": 2, "enabled": 2, "hidden": 0}
    assert client.get("/skills/alpha").json()["name"] == "甲技能"


def test_delete_unknown_skill_404(client: TestClient, skills: SkillRegistry) -> None:
    response = client.delete("/skills/nope")

    assert response.status_code == 404
    assert "技能不存在" in response.json()["detail"]


def test_delete_hidden_skill_again_404(client: TestClient, skills: SkillRegistry) -> None:
    """已经删过的再删一次 = 不存在（界面不该出现这种入口，接口也不该假装成功）。"""
    assert client.delete("/skills/alpha").status_code == 200

    second = client.delete("/skills/alpha")

    assert second.status_code == 404
    assert "技能不存在" in second.json()["detail"]


def test_restore_skill_not_hidden_404(client: TestClient, skills: SkillRegistry) -> None:
    response = client.post("/skills/alpha/restore")

    assert response.status_code == 404
    assert "不在隐藏清单" in response.json()["detail"]


def test_hidden_builtin_id_cannot_be_reused(
    client: TestClient, skills: SkillRegistry, tmp_path: Path
) -> None:
    """隐藏的内置技能仍占着 id（装载时内置优先）：查重必须看得见隐藏项。"""
    client.delete("/skills/alpha")

    response = client.post(
        "/skills",
        json={"id": "alpha", "name": "冒牌货", "body": "# 冒牌\n\n这份说明永远不会生效。\n"},
    )

    assert response.status_code == 400
    assert "内置" in response.json()["detail"]
    assert not (tmp_path / "user" / "alpha").exists()


def test_catalog_keeps_hidden_entry_with_flag(
    client: TestClient, skills: SkillRegistry
) -> None:
    """清单**含隐藏项**（带 `deleted` 标记）：界面靠它列出「已隐藏」并提供恢复入口。"""
    client.delete("/skills/alpha")

    body = client.get("/skills").json()
    by_id = {item["id"]: item for item in body["skills"]}

    assert set(by_id) == {"alpha", "beta"}
    assert by_id["alpha"]["deleted"] is True
    assert by_id["beta"]["deleted"] is False
