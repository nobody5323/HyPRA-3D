"""worldbook 注入编排：把 matcher 命中结果按**注入档位**分组，并守住 token 预算。

为什么分档（`AGENTS.md §8.4` / §8.5）：外部世界书（酒馆）的条目自带 `position`
与 `depth`——「插在人设前」与「插在对话历史倒数第 4 条之前」是两种完全不同的语义，
后者靠**临近输入**施加强影响。全部塞进同一个「场景补充」块等于把这份信息丢掉。

档位落点（对齐 ST 的 `world_info_position`）：

    0 before_char  → 人设前的「背景设定」块
    1 after_char   → 人设后的「场景补充」块（本项目原有的唯一位置）
    2/3 ANTop/ANBottom → **本项目没有作者注槽位**，归入场景补充块并记警告
    4 at_depth     → 按 depth 插进对话历史（`insert_at_depth`）
    5/6 EMTop/EMBottom → 示例对话由文风预设提供、与角色卡无关，归入场景补充块并记警告
    7 outlet       → 不进提示词（在映射层就已跳过）

预算：**所有档位共用一份世界书预算**（`WorldbookPlacement` 的默认语义），
但**在非空档位之间平分**——先到先得的话，条目多的档位会把别的档位饿死
（实测数据里 before_char 149 条、at_depth 70 条，前者会吃光整个预算）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.prompts.renderer import estimate_tokens
from app.worldbook.models import (
    POSITION_AT_DEPTH,
    POSITION_BEFORE_CHAR,
    WorldBookEntry,
)

#: 归入「场景补充」块的档位（本项目没有作者注 / 示例消息槽位，只能降级）
_FOLDED_POSITIONS: dict[str, str] = {
    "before_an": "作者注",
    "after_an": "作者注",
    "em_top": "示例消息",
    "em_bottom": "示例消息",
}


@dataclass(frozen=True)
class DepthInjection:
    """一条按深度插入对话历史的注入（`at_depth` 档）。"""

    depth: int
    text: str
    role: str = "system"


@dataclass
class WorldbookPlacement:
    """一次命中结果按档位分好的落点。"""

    before_char: str = ""
    after_char: str = ""
    depth: list[DepthInjection] = field(default_factory=list)
    skipped: list[WorldBookEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.before_char.strip() or self.after_char.strip() or self.depth)


def _block(entry: WorldBookEntry) -> str:
    return f"[{entry.title}]\n{entry.content}"


def _fit_entries(
    entries: Sequence[WorldBookEntry], budget: int
) -> tuple[list[WorldBookEntry], list[WorldBookEntry]]:
    """按预算贪心装入条目，返回 (装入的, 跳过的)。

    调用方已按 `(-priority, order)` 排好序，这里只负责省钱。
    """
    kept: list[WorldBookEntry] = []
    skipped: list[WorldBookEntry] = []
    used = 0
    for entry in entries:
        cost = estimate_tokens(_block(entry))
        if used + cost > budget:
            skipped.append(entry)
            continue
        kept.append(entry)
        used += cost
    return kept, skipped


def _split_budget(total: int, groups: int) -> int:
    """把世界书预算平摊到各非空档位（至少 1，免得全被凑整成 0）。"""
    return max(1, total // groups) if groups else total


def place_worldbook(
    hits: Iterable[WorldBookEntry],
    budget: int,
) -> WorldbookPlacement:
    """把命中条目按档位铺到各自的落点，返回编排结果。

    参数:
        hits: matcher 的命中结果（任意顺序，本函数自行按 `(-priority, order)` 排序）；
        budget: **世界书总预算**，在非空档位之间平分（见模块文档）。
    """
    ordered = sorted(hits, key=lambda e: (-e.priority, e.order))
    grouped: dict[str, list[WorldBookEntry]] = {
        POSITION_BEFORE_CHAR: [],
        # 场景补充块收下「人设后」以及所有没有对应槽位的档位
        "after_char": [],
        POSITION_AT_DEPTH: [],
    }
    folded: dict[str, int] = {}
    for entry in ordered:
        if entry.position == POSITION_BEFORE_CHAR:
            grouped[POSITION_BEFORE_CHAR].append(entry)
            continue
        if entry.position == POSITION_AT_DEPTH:
            grouped[POSITION_AT_DEPTH].append(entry)
            continue
        # 其余档位一律进场景补充块；其中没有对应槽位的那几个额外记一笔
        grouped["after_char"].append(entry)
        label = _FOLDED_POSITIONS.get(entry.position)
        if label:
            folded[label] = folded.get(label, 0) + 1

    non_empty = [name for name, items in grouped.items() if items]
    share = _split_budget(budget, len(non_empty))

    placement = WorldbookPlacement()
    for name in non_empty:
        kept, dropped = _fit_entries(grouped[name], share)
        placement.skipped.extend(dropped)
        if name == POSITION_BEFORE_CHAR:
            placement.before_char = "\n\n".join(_block(e) for e in kept)
        elif name == POSITION_AT_DEPTH:
            placement.depth = [
                DepthInjection(depth=e.depth, text=_block(e), role=e.role) for e in kept
            ]
        else:
            placement.after_char = "\n\n".join(_block(e) for e in kept)

    # 跳过的条目按原注入次序回传，便于界面/调试逐条解释「为什么没进去」
    placement.skipped.sort(key=lambda e: (-e.priority, e.order))
    for label, count in sorted(folded.items()):
        placement.warnings.append(
            f"{count} 条「{label}」档的条目没有对应槽位，已并入场景补充块"
        )
    return placement


def _anchor_floor(messages: Sequence[dict[str, str]]) -> int:
    """允许插入的最小下标：永不插到开头那一段 system 消息之前。

    对 PromptManager 而言 `messages[0]` 是**唯一的 system 提示**（人设 + 各层），
    对 ST 预设渲染器而言开头可能有若干条 system 条目。depth 再大也不能把一条
    设定插到它们前面——那等于把系统提示挤到后面，语义就反了。
    """
    floor = 0
    for message in messages:
        if message.get("role") != "system":
            break
        floor += 1
    return max(1, floor)


def insert_at_depth(
    messages: Sequence[dict[str, str]],
    injections: Sequence[DepthInjection],
) -> list[dict[str, str]]:
    """把 `at_depth` 注入插进消息列表，返回新列表。

    depth 语义（对齐 ST，也与 ST 预设渲染器一致）：
        0 = 最后一条消息**之后**；1 = 最后一条**之前**；以此类推。
    depth 超过消息条数时钳到「开头 system 段之后」（不会跑到系统提示前面）。

    同 depth 的注入按 role 合并成一条消息——与 ST 预设渲染器 `_apply_in_chat`
    的做法一致：同一位置塞多条消息只会把上下文切碎。
    """
    if not injections:
        return list(messages)

    base = list(messages)
    floor = _anchor_floor(base)
    by_anchor: dict[int, dict[str, list[str]]] = {}
    for injection in injections:
        index = max(floor, len(base) - max(0, injection.depth))
        by_anchor.setdefault(index, {}).setdefault(injection.role, []).append(
            injection.text
        )

    result: list[dict[str, str]] = []
    cursor = 0
    for index in sorted(by_anchor):
        result.extend(base[cursor:index])
        for role, texts in by_anchor[index].items():
            result.append({"role": role, "content": "\n\n".join(texts)})
        cursor = index
    result.extend(base[cursor:])
    return result
