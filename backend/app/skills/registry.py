"""技能注册表：装载、禁用、清单渲染（`AGENTS.md §9.6`）。

禁用列表落盘到 `data/skills.json`——理由与插件的启用状态完全相同：不落盘的话
重启即回弹到配置默认值，用户「关掉某个技能」的操作看起来根本没生效。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from app.prompts.renderer import estimate_tokens
from app.skills.loader import scan_skills
from app.skills.models import Skill

logger = logging.getLogger(__name__)

#: 常驻清单的 token 预算（清单本身必须便宜）
DEFAULT_CATALOG_BUDGET = 400


class SkillRegistry:
    """已装载的技能集合 + 启用状态。"""

    def __init__(self, *, state_path: str | Path = "data/skills.json") -> None:
        self.state_path = Path(state_path)
        self._skills: dict[str, Skill] = {}
        self._disabled: set[str] = set()

    # ---------------- 装载 ----------------

    def load(
        self,
        *,
        builtin_dir: str | Path = "",
        user_dir: str | Path = "",
        default_disabled: Iterable[str] = (),
    ) -> int:
        """扫描内置与用户目录并装载，返回装载数量。

        同名技能**内置优先**（与插件 `discover()` 同约定）：用户放一个同名文件
        不应该悄悄顶掉随项目分发的版本。

        启用状态从 `state.json` 恢复；没有状态文件时用 `default_disabled`（配置项）。
        """
        self._skills.clear()

        # 先内置后用户：同名时内置先占位（用户放个同名文件不该顶掉随项目分发的版本）
        candidates: list[Skill] = []
        candidates.extend(scan_skills(builtin_dir, source="builtin"))
        candidates.extend(scan_skills(user_dir, source="user"))
        for skill in candidates:
            if skill.id in self._skills:
                logger.info("技能 %s 已由内置版本提供，跳过用户版本", skill.id)
                continue
            self._skills[skill.id] = skill

        self._disabled = self._load_disabled(default_disabled)
        logger.info(
            "技能装载完成（%d 个，禁用 %d 个）", len(self._skills), len(self._disabled)
        )
        return len(self._skills)

    def _load_disabled(self, default: Iterable[str]) -> set[str]:
        fallback = {str(item).strip() for item in default if str(item).strip()}
        if not self.state_path.is_file():
            return fallback
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # 状态损坏不该阻断启动
            logger.warning("技能禁用状态读取失败（用默认值）：%s", exc)
            return fallback
        disabled = data.get("disabled") if isinstance(data, dict) else None
        # 严格判 list：手改过的标量不应被当成列表
        return {str(item) for item in disabled} if isinstance(disabled, list) else fallback

    def _save_disabled(self) -> None:
        """写禁用列表；写失败只告警——本次运行仍然生效，不该因磁盘问题阻断开关。"""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"disabled": sorted(self._disabled)}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("技能禁用状态写入失败（本次运行仍生效）：%s", exc)

    # ---------------- 查询 ----------------

    def all(self) -> list[Skill]:
        """全部技能（按 id 稳定排序）。"""
        return sorted(self._skills.values(), key=lambda skill: skill.id)

    def enabled(self) -> list[Skill]:
        return [skill for skill in self.all() if skill.id not in self._disabled]

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def is_enabled(self, skill_id: str) -> bool:
        return skill_id in self._skills and skill_id not in self._disabled

    def __len__(self) -> int:
        return len(self._skills)

    # ---------------- 启停 ----------------

    def set_enabled(self, skill_id: str, enabled: bool) -> None:
        """启用 / 禁用技能并落盘。未知 id 抛 KeyError（由 API 映射为 404）。"""
        if skill_id not in self._skills:
            raise KeyError(f"技能不存在：{skill_id}")
        if enabled:
            self._disabled.discard(skill_id)
        else:
            self._disabled.add(skill_id)
        self._save_disabled()

    # ---------------- 上下文注入 ----------------

    def catalog_text(self, budget: int = DEFAULT_CATALOG_BUDGET) -> str:
        """常驻技能清单（只有 id / 名称 / 适用场景）。

        正文**不在这里**——那是 `study_skill` 的事。清单超预算时从尾部截断：
        宁可少列几个技能，也不要让「索引」本身吃掉上下文预算。

        没有任何启用技能时返回空串（调用方据此整层跳过，不注入空标题）。
        """
        lines = [skill.catalog_line for skill in self.enabled()]
        if not lines:
            return ""

        kept: list[str] = []
        used = 0
        for line in lines:
            cost = estimate_tokens(line)
            if used + cost > budget:
                logger.info(
                    "技能清单超出预算（%d token），已截断 %d 项", budget, len(lines) - len(kept)
                )
                break
            kept.append(line)
            used += cost
        return "\n".join(kept)

    # ---------------- 视图（API / 测试）----------------

    def status(self) -> list[dict]:
        """技能列表快照（供 `/skills` 与管理 UI）。"""
        return [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "when_to_use": skill.when_to_use,
                "source": skill.source,
                "enabled": skill.id not in self._disabled,
                # 正文长度而非正文：列表接口不该把所有技能正文都吐出去
                "body_chars": len(skill.body),
            }
            for skill in self.all()
        ]


# ---------------- 全局单例（与 app.plugins.manager 同范式）----------------

_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    """取全局技能库（不存在则建空库）。"""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry


def configure_skills(
    *,
    builtin_dir: str | Path = "",
    user_dir: str | Path = "",
    state_path: str | Path = "data/skills.json",
    default_disabled: Iterable[str] = (),
) -> SkillRegistry:
    """构造并设为全局技能库（`create_app` 调用一次）。"""
    global _registry
    _registry = SkillRegistry(state_path=state_path)
    _registry.load(
        builtin_dir=builtin_dir, user_dir=user_dir, default_disabled=default_disabled
    )
    return _registry


def set_skill_registry(registry: SkillRegistry | None) -> None:
    """替换全局技能库（测试用；传 None 表示下次 get 时重建）。"""
    global _registry
    _registry = registry
