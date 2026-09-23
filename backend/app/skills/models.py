"""Skill 数据模型（`AGENTS.md §9.6`）。

Skill 是**渐进式加载**的能力说明：常驻上下文里只有一行「id：名称 —— 何时用」，
模型判断需要时调 `study_skill(id)` 才把正文取回来。

与「把方法论一次性塞进 system prompt」的区别全在 token 上：十个技能的正文
合起来可能上万 token，而清单只有几十。常驻清单 + 按需取正文，
才能既让模型知道「有什么能力」，又不把上下文吃光。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    """一个技能包（一个目录一个 `SKILL.md`）。"""

    #: 技能 id（frontmatter 的 `id`，缺省取目录名 / 文件名）
    id: str
    #: 展示名（缺省同 id）
    name: str
    #: 一句话说明它是什么
    description: str
    #: 什么情况下该用它（清单里优先展示这条）
    when_to_use: str
    #: Markdown 正文 —— **只在 `study_skill` 时注入**
    body: str
    #: 来源：builtin（入库，随项目分发）| user（data/skills，用户自己加的）
    source: str
    path: Path

    @property
    def catalog_line(self) -> str:
        """清单里的一行。

        常驻上下文，**必须短**：只放 id、名称与适用场景，不放描述——
        `when_to_use` 才是模型判断「现在该不该用」的依据。
        """
        hint = self.when_to_use or self.description
        return f"- {self.id}：{self.name} —— {hint}"
