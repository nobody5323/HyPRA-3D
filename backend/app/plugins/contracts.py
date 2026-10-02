"""数据源中性契约：插件与宿主之间的数据边界。

设计依据 `AGENTS.md §9.4` 的硬约定：

> **插件只负责「读 + 产出中性数据」，映射到宿主模型（`WorldBookEntry` / persona…）
> 一律由宿主完成**——保证多数据源（酒馆、Character.AI 导出、其他客户端…）共享同一套模型。

这样插件不需要知道 `WorldBookEntry` 长什么样，宿主也不需要知道任何外部文件格式；
两边都只依赖本模块定义的这组结构。将来换数据源，插件换、宿主映射器不换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataSourceEntry:
    """一条「可注入的设定条目」——世界书 / lorebook / 角色内嵌设定都归一到此。

    字段刻意只保留跨来源的**共性**；来源特有的东西（ST 的 `groupWeight`、
    `selectiveLogic`、`characterFilter`…）一律放 `extra` 透传，
    宿主不强解释、原样保留，避免把某一家的格式写死进契约。
    """

    title: str
    content: str
    keys: tuple[str, ...] = ()
    """触发关键词（空 + `constant=True` 表示常驻注入）。"""

    constant: bool = False
    """常驻：不依赖触发词命中（ST 世界书的 `constant`）。"""

    position: str = "after_char"
    """注入档位：`before_char` | `after_char` | `before_an` | `after_an` | `at_depth`。

    与 ST 世界书的 `position` 数值字段同构，但用字符串表达以免契约绑定某家的枚举值。
    """

    depth: int = 4
    """当 `position == "at_depth"` 时，插入到对话历史的倒数第几条之前。"""

    role: str = "system"
    """`at_depth` 档注入的消息角色：`system` | `user` | `assistant`。

    ST 世界书的 `role` 只在按深度插入时生效（默认 system）；其余档位忽略。
    """

    order: int = 100
    """同档位内的排序权重（小者先）。"""

    enabled: bool = True
    case_sensitive: bool = False
    probability: int = 100
    """触发概率（0–100）；100 表示必然触发。"""

    source: str = ""
    """来源插件 id（由宿主填入或插件自报）。"""

    source_id: str = ""
    """来源内唯一标识，用于增量同步与去重。"""

    extra: dict[str, Any] = field(default_factory=dict)
    """来源特有字段（透传，宿主不解释）。"""


@dataclass
class DataSourceCharacter:
    """一个外部角色（角色卡）——映射为宿主的 persona。"""

    name: str
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_message: str = ""
    alternate_greetings: tuple[str, ...] = ()
    example_messages: str = ""
    system_prompt: str = ""
    post_history_instructions: str = ""
    avatar_path: str = ""
    """本地头像/立绘文件路径（可空）；宿主自行决定是否使用。"""

    source: str = ""
    source_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataSourceMessage:
    """外部会话中的一条消息。"""

    role: str
    """`user` | `assistant` | `system`。"""

    text: str
    created_at: str = ""
    name: str = ""


@dataclass
class DataSourceSession:
    """一个外部会话（用于「跨会话长期记忆」的语料来源）。

    注意：会话**不属于**设定条目，它的用途是被抽取成情景记忆与语义事实，
    因此宿主对它的处理路径与 `DataSourceEntry` 完全不同。
    """

    id: str
    character_name: str = ""
    title: str = ""
    created_at: str = ""
    messages: tuple[DataSourceMessage, ...] = ()
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataSourceSnapshot:
    """一次数据源读取的完整产出。

    插件一次性返回它读到的全部内容，宿主分别投递到：
    条目 → 世界书映射器；角色 → persona 映射器；会话 → 记忆构建管线。
    """

    entries: list[DataSourceEntry] = field(default_factory=list)
    characters: list[DataSourceCharacter] = field(default_factory=list)
    sessions: list[DataSourceSession] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    """读取过程中的非致命问题（如单个文件损坏），供 UI 展示——不抛异常打断整体接入。"""

    @property
    def counts(self) -> dict[str, int]:
        return {
            "entries": len(self.entries),
            "characters": len(self.characters),
            "sessions": len(self.sessions),
        }
