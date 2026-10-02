"""记忆作用域标识符的规范化（冷层表名 / 温层与知识库 collection 名共用）。

背景（AGENTS.md §8.2）：记忆作用域键是字符串 `"{scope_type}:{scope_key}"`，
可能含冒号、连字符等字符。而下游存储对名字各有字符限制：

- Qdrant collection 名**不接受冒号**（实测返回 422 Validation error）；
- SQLite 表名不能含连字符等（会被当成 SQL 运算符）。

所以各存储层在拼名字前必须先经本模块规范化。两条硬要求：

1. **幂等**：同一作用域恒等映射到同一名字（同一作用域永远命中同一存储）；
2. **不碰撞**：含非法字符时替换并追加内容哈希后缀，避免
   `therapist-elder-sister` 与 `therapist_elder_sister` 被压成同一个名字
   —— 那会让两个陪伴对象共享记忆。

无非法字符时直接返回原值，保持名字可读（表名 / collection 名便于人工排查）。
"""

import hashlib
import re

#: 非法字符（Qdrant collection 名与 SQLite 表名片段均只接受字母数字下划线）
_UNSAFE = re.compile(r"[^0-9A-Za-z_]")


def normalize_scope_id(scope_id: str) -> str:
    """把作用域 id 规范化为合法且唯一的名字片段。

    无非法字符时直接返回（可读）：
        therapist → therapist
    含非法字符时替换为下划线并追加短哈希（防碰撞）：
        therapist-elder-sister → therapist_elder_sister_999284
        therapist_elder_sister → therapist_elder_sister   # 与上者不同
    """
    slug = _UNSAFE.sub("_", scope_id)
    if slug == scope_id:
        return slug
    digest = hashlib.md5(scope_id.encode("utf-8")).hexdigest()[:6]
    return f"{slug}_{digest}"
