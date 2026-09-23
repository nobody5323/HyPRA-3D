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
    registry.load(builtin_dir=root)
    original = get_skill_registry()
    set_skill_registry(registry)
    try:
        yield registry
    finally:
        set_skill_registry(original)


def test_list_skills(client: TestClient, skills: SkillRegistry) -> None:
    body = client.get("/skills").json()

    assert body["summary"] == {"total": 2, "enabled": 2}
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


def test_health_exposes_skills(client: TestClient, skills: SkillRegistry) -> None:
    """能力中心的面板从 /health 拿列表，不必额外发一次请求。"""
    body = client.get("/health").json()

    assert isinstance(body["skills"], list)
    assert {item["id"] for item in body["skills"]} == {"alpha", "beta"}
