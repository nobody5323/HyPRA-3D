"""世界书触发匹配器（关键词 + 正则 + 语义向量三通道）。

规则（对齐设计参照 1.2）：
- 仅 enabled 的条目参与匹配；
- 仅**归属当前陪伴对象**的条目参与匹配（`scope="*"` 对所有人生效，填角色 id
  则只对那个角色生效，见 WorldBookEntry.scope）；
- 关键词：任一命中即触发；默认不区分大小写，条目可自行声明 case_sensitive；
- 正则：条目 regex 列表中任一模式 search 命中即触发；
- 向量：由 vector_index 一次性算出语义命中的条目 id 集合
  （对应 SillyTavern 条目 Vectorized 后经向量检索命中的机制，见 vector_index.py）；
- 三通道是「或」的关系，任一命中即算条目命中；未传 vector_index 时
  **仅前两通道生效**，行为与加入向量通道前完全一致；
- 命中结果按 priority 降序排列，供后续 PromptManager 注入编排使用。
"""

import re
from collections.abc import Iterable

from app.worldbook.models import WorldBookEntry
from app.worldbook.vector_index import WorldBookVectorIndex


def matched_keys(entry: WorldBookEntry, text: str) -> list[str]:
    """关键词通道：命中的关键词列表（空列表 = 未命中）。

    对外公开是为了让界面「试触发」能逐通道说明原因：只回一个 bool 的话，
    用户看到的永远是「没命中」，却不知道为什么。
    """
    if not entry.keys or not text:
        return []
    if entry.case_sensitive:
        return [key for key in entry.keys if key in text]
    lowered = text.lower()
    return [key for key in entry.keys if key.lower() in lowered]


def matched_regex(entry: WorldBookEntry, text: str) -> list[str]:
    """正则通道：命中的模式列表（空列表 = 未命中）。"""
    hits: list[str] = []
    for pattern in entry.regex:
        try:
            if re.search(pattern, text):
                hits.append(pattern)
        except re.error:
            # 写入路径已拦下非法正则；这里兜底，避免手工改坏的 YAML
            # 让「试触发」直接 500
            continue
    return hits


def match_entries(
    entries: Iterable[WorldBookEntry],
    text: str,
    *,
    include_disabled: bool = False,
    vector_index: WorldBookVectorIndex | None = None,
    companion_id: str = "",
) -> list[WorldBookEntry]:
    """对给定文本匹配世界书条目，返回按 priority 降序的命中列表。

    参数:
        include_disabled: 是否把 enabled=False 的条目也纳入匹配
            （默认 False：停用条目直接跳过）；
        vector_index: 语义向量索引；None 时关闭向量通道（行为同两通道版本）；
        companion_id: 当前陪伴对象 id，用于按条目 `scope` 过滤归属。
            **缺省空串 = 只匹配全局条目**——这是刻意的 fail-safe：调用方忘了传
            角色 id 时宁可少注入，也不能让某个角色的专属设定串味到别的角色。
    """
    vector_hits = vector_index.match(text) if vector_index is not None else set[str]()

    hits: list[WorldBookEntry] = []
    for entry in entries:
        if not include_disabled and not entry.enabled:
            continue
        # 归属过滤放在命中判定之前：向量通道按 id 命中，这里拦不住的话
        # 专属条目会从向量通道漏进别人的上下文
        if not entry.applies_to(companion_id):
            continue
        if (
            entry.id in vector_hits
            or matched_keys(entry, text)
            or matched_regex(entry, text)
        ):
            hits.append(entry)
    # 稳定排序：priority 高者在前；同优先级保持加载顺序
    hits.sort(key=lambda e: e.priority, reverse=True)
    return hits
