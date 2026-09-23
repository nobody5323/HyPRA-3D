"""skills 子包：渐进式加载的能力说明（`AGENTS.md §9.6`）。

| 模块 | 职责 |
|---|---|
| `models.py` | `Skill` 数据模型（id / 名称 / 适用场景 / 正文） |
| `loader.py` | `SKILL.md` 的 frontmatter 解析与目录扫描 |
| `registry.py` | 装载、启停（落盘）、常驻清单渲染、全局单例 |

**与插件体系相互独立**：插件是「代码能力可插拔」（进程级），skill 是
「方法论可插拔」（提示词级）。skill 不需要注册能力面、不落 `PluginContext`，
也不受分层（core / builtin）约束——它只是给模型看的一段说明。

注入的两半：
- **常驻清单**由 `registry.catalog_text()` 产出，消费方是 `PromptManager` 的 skills 层；
- **正文**由 `study_skill` 工具按需取回（`app/tools/skills.py`），当轮生效。
"""

from app.skills.models import Skill
from app.skills.registry import (
    DEFAULT_CATALOG_BUDGET,
    SkillRegistry,
    configure_skills,
    get_skill_registry,
    set_skill_registry,
)

__all__ = [
    "DEFAULT_CATALOG_BUDGET",
    "Skill",
    "SkillRegistry",
    "configure_skills",
    "get_skill_registry",
    "set_skill_registry",
]
