"""技能注册表：装载、删除、禁用、清单渲染（`AGENTS.md §9.6`）。

状态落盘到 `data/skills.json`，两份清单各管一件事：

- `disabled`：**启停**偏好。禁用的技能仍在列表里（灰着），模型取不到正文；
- `deleted`：**隐藏**清单。只收内置技能——内置是随包分发的第一方内容，
  运行时永不删改（§9.12 硬规则 2），所以「删除内置技能」的语义只能是
  「写进用户侧隐藏清单，包内文件一字未改」，之后可以恢复。

自建技能（`data/skills/`）的删除是**物理删除**：那是用户自己的文件，
留着一条隐藏记录反而会在用户用同一个 id 新建技能时把它悄悄吞掉。

不落盘的话重启即回弹到配置默认值，用户「关掉/删掉某个技能」的操作看起来根本没生效。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path

from app.prompts.renderer import estimate_tokens
from app.skills.loader import scan_skills
from app.skills.models import Skill
from app.skills.writer import delete_skill

logger = logging.getLogger(__name__)

#: 常驻清单的 token 预算（清单本身必须便宜）
DEFAULT_CATALOG_BUDGET = 400


class SkillRegistry:
    """已装载的技能集合 + 启用状态 + 隐藏清单。"""

    def __init__(self, *, state_path: str | Path = "data/skills.json") -> None:
        self.state_path = Path(state_path)
        self._skills: dict[str, Skill] = {}
        self._disabled: set[str] = set()
        #: 被「删除」的内置技能 id（文件仍在，只是不再出现，可恢复）
        self._deleted: set[str] = set()
        #: 用户技能目录（删除自建技能时要知道删哪儿；`load` 时记下）
        self._user_dir: str = ""

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

        启用状态与隐藏清单从状态文件恢复；没有状态文件时用 `default_disabled`（配置项）。
        """
        self._skills.clear()
        self._user_dir = str(user_dir).strip()

        # 先内置后用户：同名时内置先占位（用户放个同名文件不该顶掉随项目分发的版本）
        candidates: list[Skill] = []
        candidates.extend(scan_skills(builtin_dir, source="builtin"))
        candidates.extend(scan_skills(user_dir, source="user"))
        for skill in candidates:
            if skill.id in self._skills:
                logger.info("技能 %s 已由内置版本提供，跳过用户版本", skill.id)
                continue
            self._skills[skill.id] = skill

        state = self._read_state()
        self._disabled = self._read_id_set(state, "disabled", default_disabled)
        self._deleted = self._read_id_set(state, "deleted")
        logger.info(
            "技能装载完成（%d 个，禁用 %d 个，隐藏 %d 个）",
            len(self._skills),
            len(self._disabled),
            len(self._deleted),
        )
        return len(self._skills)

    def _read_state(self) -> dict:
        """读状态文件（缺失 / 损坏返回空表——状态问题不该阻断启动）。"""
        if not self.state_path.is_file():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # 状态损坏不该阻断启动
            logger.warning("技能状态读取失败（用默认值）：%s", exc)
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _read_id_set(state: dict, key: str, default: Iterable[str] = ()) -> set[str]:
        """取一个 id 列表字段；**严格判 list**（手改过的标量不应被当成列表）。"""
        fallback = {str(item).strip() for item in default if str(item).strip()}
        values = state.get(key)
        return {str(item) for item in values} if isinstance(values, list) else fallback

    def _save_state(self) -> None:
        """写状态；写失败只告警——本次运行仍然生效，不该因磁盘问题阻断开关。

        `deleted` 为空时**不写这个键**：空清单没有信息量，而多出来的空数组会让
        「手改过状态文件」和「程序写的」看起来一样。
        """
        payload: dict[str, list[str]] = {"disabled": sorted(self._disabled)}
        if self._deleted:
            payload["deleted"] = sorted(self._deleted)
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("技能状态写入失败（本次运行仍生效）：%s", exc)

    # ---------------- 查询 ----------------

    def all(self) -> list[Skill]:
        """全部**可见**技能（按 id 稳定排序；隐藏的不算）。"""
        return [
            skill
            for skill in sorted(self._skills.values(), key=lambda item: item.id)
            if skill.id not in self._deleted
        ]

    def enabled(self) -> list[Skill]:
        return [skill for skill in self.all() if skill.id not in self._disabled]

    def hidden(self) -> list[Skill]:
        """被删除（=隐藏）的内置技能，供界面给一个「恢复」的入口。"""
        return [skill for skill in self.all_including_hidden() if skill.id in self._deleted]

    def all_including_hidden(self) -> list[Skill]:
        """**含隐藏**的全部技能（按 id 排序）。

        只有两个地方该用它：`status()`（界面要能列出隐藏项才能恢复）与
        `hidden()`。对话侧的清单注入一律走 `all()` / `enabled()`——
        删掉的技能不能因为「文件还在」就继续出现在模型眼前。
        """
        return sorted(self._skills.values(), key=lambda skill: skill.id)

    def get(self, skill_id: str) -> Skill | None:
        """按 id 取技能；隐藏的按「不存在」处理（与禁用一致：不能从侧门绕过）。"""
        if skill_id in self._deleted:
            return None
        return self._skills.get(skill_id)

    def get_including_hidden(self, skill_id: str) -> Skill | None:
        """按 id 取技能，**含隐藏项**。

        只有「写入前查重」需要它：id 被一个**已隐藏**的内置技能占着时，
        新写的用户技能照样读不到（装载时内置优先），所以查重必须看得见隐藏项。
        """
        return self._skills.get(skill_id)

    def is_enabled(self, skill_id: str) -> bool:
        return (
            skill_id in self._skills
            and skill_id not in self._deleted
            and skill_id not in self._disabled
        )

    def __len__(self) -> int:
        return len(self.all())

    # ---------------- 启停 / 删除 ----------------

    def set_enabled(self, skill_id: str, enabled: bool) -> None:
        """启用 / 禁用技能并落盘。未知 id 抛 KeyError（由 API 映射为 404）。"""
        if skill_id not in self._skills or skill_id in self._deleted:
            raise KeyError(f"技能不存在：{skill_id}")
        if enabled:
            self._disabled.discard(skill_id)
        else:
            self._disabled.add(skill_id)
        self._save_state()

    def delete(self, skill_id: str) -> tuple[Skill, Path | None]:
        """删除技能，返回 `(被删的技能, 磁盘上被删掉的路径)`。

        两种来源两条路，**都不是「一律删文件」**：

        - 内置（`backend/skills/`）：写进用户侧隐藏清单，包内文件一字未改
          （§9.12 硬规则 2）。`Path` 返回 None——什么都没从磁盘上消失。
        - 自建（`USER_SKILLS_DIR`）：那是用户自己的文件，物理删除。

        两条路都同步内存，所以调用方**不需要**再重扫一次。

        抛出:
            KeyError: id 未知或已在隐藏清单里（API 映射为 404）。
            SkillWriteError: 自建技能删除失败（越界 / 被占用 / 无权限）。
        """
        skill = self.get_including_hidden(skill_id)
        if skill is None or skill_id in self._deleted:
            raise KeyError(f"技能不存在：{skill_id}")

        if skill.source == "builtin":
            self._deleted.add(skill_id)
            # **不动 `disabled`**：用户先禁用再删除、之后恢复，应当回到「禁用」那个状态；
            # 顺手抹掉反而把用户明确表达过的偏好吞了
            self._save_state()
            logger.info("内置技能已隐藏（包内文件未改）：%s", skill_id)
            return skill, None

        removed = delete_skill(self._user_dir, skill)
        self._skills.pop(skill_id, None)
        # 自建技能删掉后，那条禁用记录必须一起清：否则用户日后用同一个 id 新建技能，
        # 新技能会一出生就是「禁用」状态（而界面上找不到原因）
        self._disabled.discard(skill_id)
        self._save_state()
        return skill, removed

    def restore(self, skill_id: str) -> Skill:
        """把隐藏清单里的内置技能恢复出来。

        只对隐藏项有效：自建技能是物理删除，没有可恢复的对象（要恢复就是再写一份）。

        抛出:
            KeyError: id 不在隐藏清单里，或清单里的 id 在磁盘上已经不存在
                （随包版本变了——顺手清掉这条死记录）。
        """
        if skill_id not in self._deleted:
            raise KeyError(f"技能不在隐藏清单里：{skill_id}")
        skill = self.get_including_hidden(skill_id)
        if skill is None:
            self._deleted.discard(skill_id)
            self._save_state()
            raise KeyError(f"技能不存在：{skill_id}")
        self._deleted.discard(skill_id)
        self._save_state()
        logger.info("技能已恢复：%s", skill_id)
        return skill

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
        """技能列表快照（供 `/skills` 与管理 UI）。

        **含隐藏项**（`deleted: true`）：界面要把它们单独列成「已隐藏」才能提供
        恢复入口。但常驻清单与 `study_skill` 走 `all()` / `enabled()`，
        与这里无关——删掉的技能不会再出现在模型眼前。
        """
        return [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "when_to_use": skill.when_to_use,
                "source": skill.source,
                "enabled": skill.id not in self._disabled and skill.id not in self._deleted,
                "deleted": skill.id in self._deleted,
                # 正文长度而非正文：列表接口不该把所有技能正文都吐出去
                "body_chars": len(skill.body),
            }
            for skill in self.all_including_hidden()
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
