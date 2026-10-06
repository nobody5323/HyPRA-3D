"""知识库作用域口径测试。

这是「写入侧与检索侧共用同一套函数」的守门用例：两边各算各的键，
内容就会挂在一个永远匹配不上的作用域上（表现为「明明导了却召不回」）。

**每本世界书一个独立作用域**（`tavern:book:{哈希}`），全局书与角色内嵌书一视同仁；
**挂载清单决定查哪几本**，默认空 = 一本都不查（见 `retrieval_scopes`）。
"""

from app.memory.knowledge.scopes import (
    BOOK_SCOPE_PREFIX,
    TAVERN_SCOPE,
    base_scopes,
    book_scope,
    is_legacy_tavern_scope,
    is_tavern_scope,
    retrieval_scopes,
)
from app.worldbook.models import SCOPE_ALL


def test_book_scope_is_per_source_and_stable() -> None:
    """同一来源恒等映射到同一作用域；不同来源不碰撞。"""
    a = book_scope("world/咒术回战同人创作库")
    b = book_scope("world/咒术回战同人创作库")
    c = book_scope("world/另一个世界书")

    assert a == b
    assert a != c
    assert a.startswith(f"{TAVERN_SCOPE}:{BOOK_SCOPE_PREFIX}:")


def test_book_scope_separates_global_and_embedded_books() -> None:
    """全局书与角色内嵌书各占一个作用域——不再分「共享 / 角色专属」两套待遇。"""
    assert book_scope("world/甲") != book_scope("char/甲")


def test_is_tavern_scope_only_matches_new_format() -> None:
    assert is_tavern_scope(book_scope("world/甲")) is True
    assert is_tavern_scope(f"{TAVERN_SCOPE}") is False
    assert is_tavern_scope(f"{TAVERN_SCOPE}:tbp-abc") is False
    assert is_tavern_scope("user-21fb96") is False


def test_is_legacy_tavern_scope_matches_old_keys() -> None:
    """旧格式（裸 `tavern` / `tavern:{角色id}`）能被识别，供清理逻辑删除。"""
    assert is_legacy_tavern_scope(TAVERN_SCOPE) is True
    assert is_legacy_tavern_scope(f"{TAVERN_SCOPE}:tbp-abc") is True
    # 新格式不算旧
    assert is_legacy_tavern_scope(book_scope("world/甲")) is False
    # 无关作用域也不算
    assert is_legacy_tavern_scope("user-21fb96") is False
    assert is_legacy_tavern_scope(SCOPE_ALL) is False


def test_base_scopes_never_duplicate_the_shared_scope() -> None:
    assert base_scopes("tbp-abc") == ["tbp-abc", SCOPE_ALL]
    assert base_scopes(SCOPE_ALL) == [SCOPE_ALL]
    assert base_scopes("") == [SCOPE_ALL]


def test_retrieval_scopes_default_mounts_nothing() -> None:
    """★ 默认全不挂：酒馆模式下也不查任何世界书作用域。"""
    tavern_mode = retrieval_scopes("tbp-abc", include_tavern=True)
    assert tavern_mode == ["tbp-abc", SCOPE_ALL]
    assert all(not is_tavern_scope(scope) for scope in tavern_mode)


def test_retrieval_scopes_only_queries_mounted_books() -> None:
    """★ 挂哪几本查哪几本，且与 `book_scope()` 对齐（写入/检索同一套键）。"""
    mounted = ["world/咒术回战同人创作库", "char/咒术回战"]
    scopes = retrieval_scopes("tbp-abc", include_tavern=True, mounted_books=mounted)

    assert scopes == [
        "tbp-abc",
        SCOPE_ALL,
        book_scope("world/咒术回战同人创作库"),
        book_scope("char/咒术回战"),
    ]


def test_retrieval_scopes_gate_tavern_by_mode() -> None:
    """★ 桌宠模式（include_tavern=False）即使挂了书也一个世界书作用域都不查。"""
    mounted = ["world/甲"]

    companion_mode = retrieval_scopes("tbp-abc", include_tavern=False, mounted_books=mounted)
    tavern_mode = retrieval_scopes("tbp-abc", include_tavern=True, mounted_books=mounted)

    assert companion_mode == ["tbp-abc", SCOPE_ALL]
    assert all(not is_tavern_scope(scope) for scope in companion_mode)
    assert book_scope("world/甲") in tavern_mode
