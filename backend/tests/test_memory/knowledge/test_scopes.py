"""知识库作用域口径测试。

这是「写入侧与检索侧共用同一套函数」的守门用例：两边各算各的键，
内容就会挂在一个永远匹配不上的作用域上（表现为「明明导了却召不回」）。
"""

from app.memory.knowledge.scopes import (
    TAVERN_SCOPE,
    base_scopes,
    retrieval_scopes,
    tavern_scope,
    tavern_scopes,
)
from app.worldbook.models import SCOPE_ALL


def test_tavern_scope_of_global_book_is_the_bare_prefix() -> None:
    """全局书（不属于任何角色卡）用裸前缀，所有酒馆会话共用一份。"""
    assert tavern_scope("") == TAVERN_SCOPE
    assert tavern_scope(SCOPE_ALL) == TAVERN_SCOPE


def test_tavern_scope_of_embedded_book_is_per_character() -> None:
    assert tavern_scope("tbp-abc") == f"{TAVERN_SCOPE}:tbp-abc"


def test_tavern_scopes_are_ordered_and_deduped() -> None:
    """全局书在前、本角色内嵌书在后，且不重复。"""
    assert tavern_scopes("tbp-abc") == [TAVERN_SCOPE, f"{TAVERN_SCOPE}:tbp-abc"]
    assert tavern_scopes(TAVERN_SCOPE) == [TAVERN_SCOPE]


def test_base_scopes_never_duplicate_the_shared_scope() -> None:
    assert base_scopes("tbp-abc") == ["tbp-abc", SCOPE_ALL]
    assert base_scopes(SCOPE_ALL) == [SCOPE_ALL]
    assert base_scopes("") == [SCOPE_ALL]


def test_retrieval_scopes_gate_tavern_by_flag() -> None:
    """★ 开关就是「是不是酒馆聊天模式」：关着时一个 tavern 作用域都不查。"""
    companion = "tbp-abc"

    companion_mode = retrieval_scopes(companion, include_tavern=False)
    tavern_mode = retrieval_scopes(companion, include_tavern=True)

    assert all(not scope.startswith(TAVERN_SCOPE) for scope in companion_mode)
    assert companion_mode == [companion, SCOPE_ALL]
    assert tavern_mode == [
        companion,
        SCOPE_ALL,
        TAVERN_SCOPE,
        f"{TAVERN_SCOPE}:{companion}",
    ]
