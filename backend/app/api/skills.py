"""Skill 管理 API（设计见 `AGENTS.md §9.6`）。

    GET  /skills              技能清单（含启用状态，**不含正文**）
    GET  /skills/{id}         技能详情（含 Markdown 正文，供界面预览）
    POST /skills/{id}/enabled 启用 / 禁用（落盘，重启后保持）
    POST /skills/reload       重新扫描技能目录（用户手写新技能后免重启）

**不挂在 `/plugins` 下**：插件是「代码能力可插拔」（进程级），skill 是
「方法论可插拔」（提示词级）。两者的生命周期、权限模型、分层规则都不一样，
放进同一个命名空间只会让两个概念互相污染。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings, skills_disabled_list
from app.skills.registry import configure_skills, get_skill_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillToggleRequest(BaseModel):
    enabled: bool = Field(description="是否启用")


def _summary(registry) -> dict:
    return {"total": len(registry), "enabled": len(registry.enabled())}


@router.get("")
def list_skills() -> dict:
    """技能清单。只给元信息——正文可能上万 token，列表接口不该把它吐出来。"""
    registry = get_skill_registry()
    return {"skills": registry.status(), "summary": _summary(registry)}


@router.post("/reload")
def reload_skills() -> dict:
    """重新扫描技能目录。

    技能是**文件形态**的（用户随时可能往 `data/skills/` 丢一个 SKILL.md），
    没有理由逼他们重启后端——这与 §9.7「插件不做热重载」不矛盾：
    那边重载要重跑插件代码，这边只是重新读几个 Markdown 文件。
    """
    settings = get_settings()
    registry = configure_skills(
        builtin_dir=settings.skills_dir,
        user_dir=settings.user_skills_dir,
        state_path=settings.skills_state_file,
        default_disabled=skills_disabled_list(settings),
    )
    return {"skills": registry.status(), "summary": _summary(registry)}


@router.get("/{skill_id}")
def get_skill(skill_id: str) -> dict:
    """技能详情（含正文，供界面展开预览）。"""
    registry = get_skill_registry()
    skill = registry.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"技能不存在：{skill_id}")
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "when_to_use": skill.when_to_use,
        "source": skill.source,
        "enabled": registry.is_enabled(skill_id),
        "body": skill.body,
    }


@router.post("/{skill_id}/enabled")
def set_skill_enabled(skill_id: str, payload: SkillToggleRequest) -> dict:
    """启用 / 禁用技能（落盘，重启后保持）。"""
    registry = get_skill_registry()
    try:
        registry.set_enabled(skill_id, payload.enabled)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"技能不存在：{skill_id}") from exc

    return {"id": skill_id, "enabled": payload.enabled, "skills": registry.status()}
