"""记忆作用域命名规范化测试（app/memory/naming.py + 各存储层拼名）。

**为什么必须有这组测试**：Qdrant collection 名不接受冒号（实测 422），
而此前所有测试都走 `InMemory*Store`（dict key 无字符限制）或
`QdrantWarmStore(local_path=":memory:")`（QdrantLocal 是纯 Python 实现，
同样不校验名字）——所以「作用域键含非法字符」这个缺陷完全不被覆盖，
只有真 Qdrant 服务才会暴露。

因此这里直接对**生成的名字**下断言，不依赖外部服务：
只要名字合法且唯一，真服务上就不会出问题。
"""

import re

import pytest

from app.memory.naming import normalize_scope_id

#: 合法名字字符集（Qdrant collection 名与 SQLite 表名片段均安全）
_SAFE = re.compile(r"[0-9A-Za-z_]+")


# ---------- normalize_scope_id ----------


@pytest.mark.parametrize(
    ("scope_id", "expected"),
    [
        # 无非法字符：原样返回，保持可读（便于排查）
        ("therapist", "therapist"),
        ("therapist_elder_sister", "therapist_elder_sister"),
        # 含非法字符：替换 + 6 位哈希
        # ⚠️ 这些值 pin 住：一旦变化等于已有数据改名，必须配迁移脚本
        ("therapist-elder-sister", "therapist_elder_sister_999284"),
        ("companion:abc", "companion_abc_18a4a1"),
        ("session:sess-123", "session_sess_123_2eabec"),
    ],
)
def test_normalize_pinned_values(scope_id: str, expected: str) -> None:
    assert normalize_scope_id(scope_id) == expected


@pytest.mark.parametrize(
    "scope_id",
    ["therapist", "therapist-elder-sister", "companion:abc", "中文名", "a b", ""],
)
def test_normalize_is_idempotent(scope_id: str) -> None:
    """规范化结果再规范化应恒等（同一作用域永远命中同一存储）。"""
    once = normalize_scope_id(scope_id)
    assert normalize_scope_id(once) == once


@pytest.mark.parametrize(
    "scope_id",
    ["a:b", "a-b", "a.b", "a b", "中文名", "companion:abc", ""],
)
def test_normalize_output_has_only_safe_chars(scope_id: str) -> None:
    """输出只含字母数字下划线（空串是唯一例外，由上层校验拦截）。"""
    out = normalize_scope_id(scope_id)
    assert out == "" or _SAFE.fullmatch(out)


def test_normalize_does_not_collide() -> None:
    """易混 id 必须区分开。

    若只做字符替换，这些会压成同名 —— 两个陪伴对象将共享同一份记忆，
    属于数据串扰，比报错更危险。
    """
    pairs = [
        ("therapist-elder-sister", "therapist_elder_sister"),
        ("a.b", "a-b"),
        ("session:x", "session_x"),
        ("companion:a-b", "companion:a_b"),
    ]
    for left, right in pairs:
        assert normalize_scope_id(left) != normalize_scope_id(right)


# ---------- 各存储层拼名（不依赖外部服务）----------


def test_warm_collection_name_is_safe() -> None:
    """温层 collection 名不得含冒号（AGENTS.md §8.2 的 scope 键含冒号）。"""
    from app.memory.warm.qdrant_store import QdrantWarmStore

    store = QdrantWarmStore(local_path=":memory:")
    name = store._collection_name("companion:abc")

    assert ":" not in name
    assert name == f"memory_{normalize_scope_id('companion:abc')}"


def test_knowledge_collection_names_are_safe() -> None:
    """个人记忆的两类 collection 同样必须规范化。"""
    from app.memory.knowledge.qdrant_store import QdrantKnowledgeStore

    store = QdrantKnowledgeStore(local_path=":memory:")
    slug = normalize_scope_id("companion:abc")

    assert store._chunk_collection("companion:abc") == f"knowledge_{slug}"
    assert store._meta_collection("companion:abc") == f"knowledge_meta_{slug}"


def test_cold_table_name_uses_shared_normalization() -> None:
    """冷层与温层共用同一规范化：同一作用域在两层得到同一 slug 片段。

    冷层原本自带一份 `_normalize`，提取到 naming.py 后必须行为不变
    （已有 SQLite 表名不能改）。
    """
    from app.memory.cold.sqlite_store import _table

    assert _table("therapist") == "facts_therapist"
    assert _table("therapist-elder-sister") == "facts_therapist_elder_sister_999284"
