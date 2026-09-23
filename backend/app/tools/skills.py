"""`study_skill` 工具：按需取回技能正文（渐进式加载的「取」这一半）。

为什么是工具而不是直接注入：技能正文合计可能上万 token，常驻会吃掉预算。
清单常驻（几十 token），模型判断确实需要某一项时再取——取回的内容作为 tool 消息
回传，**当轮生效**，不会一直挂在上下文里。

配套的「清单注入」见 `app/skills/registry.py: catalog_text()`，
消费方是 `PromptManager` 的 skills 层（`app/rag/prompt_manager.py`）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.skills.registry import SkillRegistry
from app.tools.registry import ToolContext, ToolSpec

#: `ToolContext.extras` 里的技能库键名（依赖经 extras 注入，registry 不依赖具体存储）
CTX_SKILL_REGISTRY = "skill_registry"

#: 工具名
STUDY_SKILL_NAME = "study_skill"


class StudySkillArgs(BaseModel):
    name: str = Field(description="技能 id（见系统提示里的可用技能清单）")


def _study_skill(args: StudySkillArgs, ctx: ToolContext) -> dict:
    registry: SkillRegistry | None = ctx.extras.get(CTX_SKILL_REGISTRY)
    if registry is None:
        return {"message": "技能库当前不可用。", "found": False}

    skill = registry.get(args.name)
    # 被禁用的技能同样算「不存在」：否则用户关掉它反而能被模型绕过
    if skill is None or not registry.is_enabled(args.name):
        available = "、".join(item.id for item in registry.enabled())
        return {
            "message": f"没有可用技能「{args.name}」。可用技能：{available or '（无）'}",
            "found": False,
        }

    return {
        "message": f"【{skill.name}】\n\n{skill.body}",
        "found": True,
        "skill": skill.id,
        "name": skill.name,
    }


def build_study_skill_tool() -> ToolSpec:
    """构造 `study_skill` 工具。"""
    return ToolSpec(
        name=STUDY_SKILL_NAME,
        description=(
            "取回某个技能的详细操作指引。系统提示里只列了技能名与适用场景；"
            "当你判断当前情况正对应某一项时，先调用它取回正文，再按其中的步骤回应。"
        ),
        parameters=StudySkillArgs.model_json_schema(),
        args_model=StudySkillArgs,
        handler=_study_skill,
        tags=["skill"],
    )
