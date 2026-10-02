"""Skill 管理 API（设计见 `AGENTS.md §9.6`）。

    GET    /skills              技能清单（含启用/隐藏状态，**不含正文**）
    GET    /skills/{id}         技能详情（含 Markdown 正文，供界面预览）
    POST   /skills              新建技能（写入 `USER_SKILLS_DIR`，见 §9.12）
    POST   /skills/ai-draft     用模型生成技能草稿（**不落盘**）
    POST   /skills/{id}/enabled 启用 / 禁用（落盘，重启后保持）
    DELETE /skills/{id}         删除技能（自建删文件；内置写隐藏清单，见下）
    POST   /skills/{id}/restore 恢复被隐藏的内置技能
    POST   /skills/reload        重新扫描技能目录（用户手写新技能后免重启）

**不挂在 `/plugins` 下**：插件是「代码能力可插拔」（进程级），skill 是
「方法论可插拔」（提示词级）。两者的生命周期、权限模型、分层规则都不一样，
放进同一个命名空间只会让两个概念互相污染。

**删除有两种语义**（都由 `SkillRegistry.delete` 定，不由界面自觉）：

- 自建技能（`data/skills/`）→ 物理删除用户自己的文件；
- 内置技能（`backend/skills/`）→ 只写用户侧隐藏清单，**包内文件一字未改**
  （§9.12 硬规则 2：第一方内容运行时永不改写），因此可以 `/restore` 找回。

写入只经 `app/skills/writer.py`：它只写用户目录，并在写后**回读校验**；
删除也走它（`delete_skill`），并且只认落在用户目录里的路径。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings, skills_disabled_list
from app.llm.authoring import (
    AuthoringFailed,
    AuthoringUnavailable,
    SkillDraft,
    generate_draft,
    normalize_skill_draft,
)
from app.skills.registry import SkillRegistry, configure_skills, get_skill_registry
from app.skills.writer import SkillWriteError, save_skill

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillToggleRequest(BaseModel):
    enabled: bool = Field(description="是否启用")


class SkillDraftRequest(BaseModel):
    """AI 生成技能草稿（`AGENTS.md §9.12`）。"""

    brief: str = Field(
        min_length=2,
        max_length=600,
        description="一句话需求，如「帮我把火气降下来，别让它变成争吵」",
    )


class SkillCreateRequest(BaseModel):
    """新建技能（字段与 SKILL.md 的 frontmatter 一一对应）。"""

    id: str = Field(description="技能 id（小写字母/数字/连字符）")
    name: str = Field(description="展示名")
    description: str = Field(default="", description="一句话说它是什么")
    when_to_use: str = Field(default="", description="什么情况下该用它")
    body: str = Field(description="Markdown 正文")


def _summary(registry) -> dict:
    """清单统计。`total` 只数**可见**技能，隐藏的单独报——否则「共 3 个技能」
    会和界面上只看到 2 行对不上。"""
    return {
        "total": len(registry),
        "enabled": len(registry.enabled()),
        "hidden": len(registry.hidden()),
    }


def _reload_registry() -> SkillRegistry:
    """按当前设置重扫技能目录（`/reload` 与新建技能共用）。"""
    settings = get_settings()
    return configure_skills(
        builtin_dir=settings.skills_dir,
        user_dir=settings.user_skills_dir,
        state_path=settings.skills_state_file,
        default_disabled=skills_disabled_list(settings),
    )


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
    registry = _reload_registry()
    return {"skills": registry.status(), "summary": _summary(registry)}


@router.post("/ai-draft")
def draft_skill(req: SkillDraftRequest) -> dict:
    """用**当前对话模型**生成一份技能草稿（**不落盘**，§9.12）。

    技能是写给模型看的操作手册：草稿拿回去让用户看过、改过再保存，
    否则一段没被审阅的“方法论”会直接影响之后所有对话的行为。
    """
    try:
        outcome = generate_draft(
            task="skill",
            brief=req.brief,
            model_cls=SkillDraft,
            normalize=normalize_skill_draft,
        )
    except AuthoringUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AuthoringFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info("技能草稿已生成（模型 %s）", outcome.model)
    return {"draft": outcome.draft.model_dump(), "model": outcome.model}


@router.post("")
def create_skill(req: SkillCreateRequest) -> dict:
    """新建技能：写入 `USER_SKILLS_DIR/<id>/SKILL.md`，随即重扫技能库。

    写的是**用户目录**：内置技能（`backend/skills/`）随项目分发，只读。
    id 已存在则拒绝（不静默覆盖用户已有的技能）。
    """
    settings = get_settings()
    # 与**内置**技能同名会静默失效：装载时内置优先（registry.load），用户写的那份永远
    # 不会被 study_skill 读到，而接口却会报成功。插件侧有同样的检查（known_ids），
    # 这里对齐——不然用户会看着「已保存」却怎么试都没反应。
    # 查重必须**看得见隐藏项**（`get_including_hidden`）：被隐藏的内置技能仍在磁盘上，
    # 同名的新技能照样读不到。
    existing = get_skill_registry().get_including_hidden(req.id)
    if existing is not None and existing.source == "builtin":
        raise HTTPException(
            status_code=400,
            detail=f"技能 id「{req.id}」已被内置技能占用（换个 id；内置技能随项目分发，不可覆盖）",
        )

    try:
        skill = save_skill(
            settings.user_skills_dir,
            skill_id=req.id,
            name=req.name,
            description=req.description,
            when_to_use=req.when_to_use,
            body=req.body,
        )
    except SkillWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    registry = _reload_registry()
    return {
        "id": skill.id,
        "path": str(skill.path),
        "skills": registry.status(),
        "summary": _summary(registry),
    }


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


@router.delete("/{skill_id}")
def delete_skill(skill_id: str) -> dict:
    """删除技能（`AGENTS.md §9.6`）。

    - **自建**（`USER_SKILLS_DIR`）：删掉磁盘上的文件——那是用户自己的东西，
      删了就没了（`path` 里给出被删的路径，供界面/日志说清到底删了什么）；
    - **内置**（`backend/skills/`）：写用户侧隐藏清单，包内文件一字未改，
      `hidden: true`，之后可用 `POST /skills/{id}/restore` 找回。

    **不重扫技能库**：`SkillRegistry.delete` 已经把内存同步好了（内置进隐藏清单、
    自建从装载表里摘掉），重扫只会把请求变成「顺带读一遍磁盘」——
    删除是个确定性动作，不该受此刻磁盘上别的改动影响。
    """
    registry = get_skill_registry()
    try:
        skill, removed = registry.delete(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"技能不存在：{skill_id}") from exc
    except SkillWriteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("技能已删除：%s（%s）", skill.id, removed or "仅隐藏，文件未删")
    return {
        "deleted": skill.id,
        "name": skill.name,
        "source": skill.source,
        "hidden": skill.source == "builtin",
        "path": str(removed) if removed is not None else None,
        "skills": registry.status(),
        "summary": _summary(registry),
    }


@router.post("/{skill_id}/restore")
def restore_skill(skill_id: str) -> dict:
    """恢复被隐藏的内置技能（隐藏清单里移除；自建技能没有可恢复的对象）。"""
    registry = get_skill_registry()
    try:
        skill = registry.restore(skill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    logger.info("技能已恢复：%s", skill.id)
    return {
        "restored": skill.id,
        "name": skill.name,
        "skills": registry.status(),
        "summary": _summary(registry),
    }
