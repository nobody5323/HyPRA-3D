"""知识库作用域口径（**写入侧与检索侧共用同一套函数**）。

知识库按作用域分库，同一份内容必须由写入方与检索方用**同一个函数**算出作用域键
——两边各算各的，内容就会挂在一个永远匹配不上的键上（表现为「明明导了却召不回」）。
本模块是这套键的**唯一**来源。

四类作用域：

| 作用域 | 键 | 谁能召回 |
|---|---|---|
| 陪伴对象私有 | `{persona_id}` | 只有它自己 |
| 共享（内置世界书等） | `*` | 所有陪伴对象 |
| 酒馆来源·全局书 | `tavern` | **只有酒馆聊天模式** |
| 酒馆来源·角色内嵌书 | `tavern:{persona_id}` | 只有酒馆聊天模式 + 该角色 |

**为什么酒馆来源要单独成库**（AGENTS.md §8.1 / §8.2）：两种模式定位不同——
`companion`（桌宠对话）是跨会话长期陪伴，`tavern`（酒馆聊天）是还原酒馆原生体验，
「两模式记忆默认完全隔离」。酒馆世界书若混进共享作用域 `*`，桌宠模式下的人设
（比如内置的元气室友）会突然「知道」另一部作品的角色设定：既污染角色，
又让用户以为召回坏了（实测就是这么被误判的）。

角色内嵌书不能只放 `tavern`：那会让 A 角色的专属设定被 B 角色召回，
隔离单位必须落到角色（与 §8.2 的「隔离单位从陪伴对象升级为作用域」一致）。
"""

from __future__ import annotations

from app.worldbook.models import SCOPE_ALL

#: 酒馆来源知识的作用域前缀（同时是全局书的作用域键本身）
TAVERN_SCOPE = "tavern"

#: 全局书与角色内嵌书的分隔符（`tavern:{persona_id}`）
_SCOPE_SEP = ":"


def tavern_scope(companion_id: str = "") -> str:
    """酒馆来源知识的作用域键。

    全局书（不属于任何角色卡）→ `tavern`，所有酒馆会话共用一份；
    角色内嵌书 → `tavern:{persona_id}`，只有该角色召得回。

    空串、`*` 以及前缀本身都按「全局」处理：调用方拿到空 companion_id 时宁可共用，
    也不要拼出一个 `tavern:` / `tavern:tavern` 这种谁都不匹配的键
    （后者看着像合法作用域，检索时静默返回空——最难查的那类 bug）。
    """
    key = (companion_id or "").strip()
    if not key or key in (SCOPE_ALL, TAVERN_SCOPE):
        return TAVERN_SCOPE
    return f"{TAVERN_SCOPE}{_SCOPE_SEP}{key}"


def tavern_scopes(companion_id: str) -> list[str]:
    """酒馆聊天模式要查的全部酒馆作用域（去重、保持稳定顺序）。"""
    return _dedup([TAVERN_SCOPE, tavern_scope(companion_id)])


def is_tavern_scope(scope: str) -> bool:
    """该作用域是否来自**酒馆世界书**（`tavern` 或 `tavern:{persona_id}`）。

    检索侧据此切换通道策略：酒馆来源走「稠密优先」，其余来源保持完整混合
    （理由见 `retriever._needs_sparse`）。写入侧不关心这个判断——它只按
    `tavern_scope()` 算键。
    """
    key = (scope or "").strip()
    return key == TAVERN_SCOPE or key.startswith(f"{TAVERN_SCOPE}{_SCOPE_SEP}")


def base_scopes(companion_id: str) -> list[str]:
    """两种模式都要查的基础作用域：本陪伴对象 + 共享（`*`）。"""
    return _dedup([companion_id, SCOPE_ALL])


def retrieval_scopes(companion_id: str, *, include_tavern: bool = False) -> list[str]:
    """一次知识检索要查的全部作用域（检索侧的唯一入口）。

    参数:
        include_tavern: 是否纳入酒馆来源作用域。**只有酒馆聊天模式传 True**
            —— 桌宠模式下酒馆世界书必须召不回来，否则会污染内置人设。
    """
    scopes = base_scopes(companion_id)
    if include_tavern:
        scopes = _dedup([*scopes, *tavern_scopes(companion_id)])
    return scopes


def _dedup(scopes: list[str]) -> list[str]:
    """去重且保序（`companion_id` 恰好是 `*` 时不要查两遍同一个库）。"""
    out: list[str] = []
    for scope in scopes:
        if scope and scope not in out:
            out.append(scope)
    return out
