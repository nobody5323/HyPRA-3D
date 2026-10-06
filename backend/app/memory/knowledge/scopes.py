"""知识库作用域口径（**写入侧与检索侧共用同一套函数**）。

知识库按作用域分库，同一份内容必须由写入方与检索方用**同一个函数**算出作用域键
——两边各算各的，内容就会挂在一个永远匹配不上的键上（表现为「明明导了却召不回」）。
本模块是这套键的**唯一**来源。

三类作用域：

| 作用域 | 键 | 谁能召回 |
|---|---|---|
| 陪伴对象私有 | `{persona_id}` | 只有它自己 |
| 共享（内置世界书等） | `*` | 所有陪伴对象 |
| 酒馆来源·**单本世界书** | `tavern:book:{来源哈希}` | **仅已被挂载时**（见下） |

**每本世界书一个独立作用域**：全局书（`world/*.json`）与角色卡内嵌书
（`char/<角色名>`）**一视同仁**——都是一本可挂载的世界书，不再分「全局共享 /
角色专属」两套待遇。隔离单位从「角色」下移到**单本世界书**，因此：

- A 书与 B 书的内容永不互相污染（问 A 书的事不会召回 B 书的条目）；
- 「挂上哪几本、就只查哪几本」——挂载清单是**全局当前挂载**（一份），
  存在插件配置里（`tavern-bridge` 的 `tavern_mounted_books`）；
- **默认全不挂**：清单为空时，酒馆模式下**一本世界书都不查**
  （旧行为是全局书无条件生效，实测会让不想用它的角色被串味）。

**为什么酒馆来源仍与陪伴对象私有作用域分开**（AGENTS.md §8.1 / §8.2）：
`companion`（桌宠对话）是跨会话长期陪伴，`tavern`（酒馆聊天）是还原酒馆原生体验，
「两模式记忆默认完全隔离」。酒馆世界书若混进共享作用域 `*`，桌宠模式下的人设
（比如内置的元气室友）会突然「知道」另一部作品的角色设定：既污染角色，
又让用户以为召回坏了（实测就是这么被误判的）。所以挂载清单**只对酒馆模式生效**。
"""

from __future__ import annotations

from collections.abc import Iterable

from app.plugins.datasources import stable_item_id
from app.worldbook.models import SCOPE_ALL

#: 酒馆来源知识的作用域前缀
TAVERN_SCOPE = "tavern"

#: 单本世界书作用域的前缀（`tavern:book:{哈希}`）
BOOK_SCOPE_PREFIX = "book"

#: 作用域分段分隔符（`tavern:{段}:{段}`）
_SCOPE_SEP = ":"


def book_scope(source_file: str) -> str:
    """单本世界书的作用域键：`tavern:book:{来源哈希}`。

    `source_file` 是插件给出的来源标识：全局书 `world/<文件名>`、
    角色内嵌书 `char/<角色名>`（见 `plugin._read_worlds` / `_read_characters`）。

    用哈希而非名字直接拼：来源名带中文与 `/`、`#`、空格，不能作 collection 名，
    且改名后不应产生第二份副本（键变 = 重新同步成一本新书）。
    """
    return f"{TAVERN_SCOPE}{_SCOPE_SEP}{BOOK_SCOPE_PREFIX}{_SCOPE_SEP}{stable_item_id('bk', 'tavern', source_file)}"


def is_tavern_scope(scope: str) -> bool:
    """该作用域是否来自**酒馆世界书**（`tavern:book:{…}`）。

    检索侧据此切换通道策略：酒馆来源走「稠密优先」，其余来源保持完整混合
    （理由见 `retriever._needs_sparse`）。写入侧不关心这个判断——它只按
    `book_scope()` 算键。
    """
    key = (scope or "").strip()
    return key.startswith(f"{TAVERN_SCOPE}{_SCOPE_SEP}{BOOK_SCOPE_PREFIX}{_SCOPE_SEP}")


def is_legacy_tavern_scope(scope: str) -> bool:
    """该作用域是否为**旧版**酒馆作用域（`tavern` / `tavern:{persona_id}`）。

    旧版把多本全局书混在 `tavern`、把内嵌书挂在 `tavern:{角色id}`。改为单本
    作用域后这些键不再被写入，但盘上可能还留着——清理逻辑据此把它们删掉，
    否则旧的全局书会一直留在库里有被误召回的风险。

    注意与 `is_tavern_scope` 的区别：后者只认新格式 `tavern:book:*`。
    """
    key = (scope or "").strip()
    if key == TAVERN_SCOPE:
        return True
    return key.startswith(f"{TAVERN_SCOPE}{_SCOPE_SEP}") and not is_tavern_scope(key)


def base_scopes(companion_id: str) -> list[str]:
    """两种模式都要查的基础作用域：本陪伴对象 + 共享（`*`）。"""
    return _dedup([companion_id, SCOPE_ALL])


def retrieval_scopes(
    companion_id: str,
    *,
    include_tavern: bool = False,
    mounted_books: Iterable[str] = (),
) -> list[str]:
    """一次知识检索要查的全部作用域（检索侧的唯一入口）。

    参数:
        include_tavern: 是否允许查酒馆世界书。**只有酒馆聊天模式传 True**
            —— 桌宠模式下世界书必须召不回来，否则会污染内置人设。
        mounted_books: 当前挂载的世界书来源标识（`world/<文件名>` / `char/<角色名>`）。
            **默认空 = 一本都不挂、一本都不查**。每个来源经 `book_scope()` 换成一个
            `tavern:book:{哈希}` 作用域——与写入侧同一个函数，键必然对得上。
    """
    scopes = base_scopes(companion_id)
    if include_tavern:
        scopes = _dedup([*scopes, *(book_scope(source) for source in mounted_books)])
    return scopes


def _dedup(scopes: list[str]) -> list[str]:
    """去重且保序（`companion_id` 恰好是 `*` 时不要查两遍同一个库）。"""
    out: list[str] = []
    for scope in scopes:
        if scope and scope not in out:
            out.append(scope)
    return out
