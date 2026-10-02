"""声明式配置 → 运行时覆盖的桥测试（`AGENTS.md §9.5`）。

覆盖三类容易出错的地方：

① **同名才生效**：schema 键与 Settings 字段同名才是覆盖，插件私有配置不参与；
② **留空 = 沿用 .env**：前端清空即删键，不该把 .env 的值也"清"掉；
③ **密钥永不出现在响应/视图里**：明文只在后端存一份。
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings, clear_runtime_overrides, runtime_overrides
from app.plugins.settings_runtime import (
    apply_runtime_overrides,
    collect_runtime_overrides,
    merge_secret_fields,
    overridable_fields,
    properties_of,
    secret_fields,
    settings_view,
)

#: 与内置 embedding / tts 插件同构的 schema（键名 = Settings 字段名）
_SCHEMA = {
    "type": "object",
    "properties": {
        "embedding_provider": {"type": "string", "enum": ["deterministic", "dashscope"]},
        "embedding_api_key": {"type": "string", "format": "password", "title": "API Key"},
        "embedding_dim": {"type": "integer"},
        # 插件私有配置：不是 Settings 字段，不参与覆盖
        "tavern_dir": {"type": "string"},
    },
}


# =============================================================
# 收集：哪些值会成为运行时覆盖
# =============================================================


def test_collect_keeps_only_schema_keys_with_values() -> None:
    collected = collect_runtime_overrides(
        _SCHEMA,
        {"embedding_provider": "dashscope", "embedding_dim": 1024, "not_in_schema": "x"},
    )

    assert collected == {"embedding_provider": "dashscope", "embedding_dim": 1024}


def test_collect_treats_blank_as_absent() -> None:
    """留空 = 沿用 .env：空串 / 空白 / 空数组都不计入覆盖。"""
    collected = collect_runtime_overrides(
        _SCHEMA,
        {"embedding_provider": "   ", "embedding_api_key": "", "embedding_dim": None},
    )

    assert collected == {}


def test_collect_ignores_non_settings_keys() -> None:
    """`tavern_dir` 不是 Settings 字段：它是插件自己的配置，不会变成覆盖。

    分工：收集只筛「schema 声明过 + 有值」，**能不能覆盖由 config 的白名单裁决**——
    白名单属于宿主，不该在插件框架里再写一份。
    """
    collected = collect_runtime_overrides(_SCHEMA, {"tavern_dir": "D:/ST/data"})

    assert collected == {"tavern_dir": "D:/ST/data"}
    assert apply_runtime_overrides(collected) is False
    assert runtime_overrides() == {}


def test_collect_tolerates_broken_schema() -> None:
    """manifest 是手写 JSON：properties 写成非 dict 时不该抛错。"""
    assert collect_runtime_overrides({"properties": {"a": "not-a-dict"}}, {"a": 1}) == {}
    assert collect_runtime_overrides(None, {"a": 1}) == {}
    assert properties_of({"properties": []}) == {}


def test_apply_writes_into_runtime_layer(monkeypatch) -> None:
    """应用后 `get_settings()` 立刻看到新值（不必重启后端）。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "deterministic")
    changed = apply_runtime_overrides({"embedding_provider": "dashscope"})

    assert changed is True
    assert Settings(_env_file=None).embedding_provider == "dashscope"
    assert runtime_overrides() == {"embedding_provider": "dashscope"}


# =============================================================
# 视图：GET 看到什么
# =============================================================


def test_secret_fields_only_password_format() -> None:
    assert secret_fields(_SCHEMA) == ["embedding_api_key"]


def test_settings_view_prefills_effective_values(monkeypatch) -> None:
    """未保存的字段用「当前生效值」回填：界面显示的应当就是实际在用的。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "siliconflow")
    defaults = {"embedding_provider": "siliconflow", "embedding_dim": 768}

    values, secrets_set = settings_view(_SCHEMA, {}, defaults=defaults)

    assert values == {"embedding_provider": "siliconflow", "embedding_dim": 768}
    assert secrets_set == []


def test_settings_view_never_returns_secret_plaintext() -> None:
    """密钥字段的值永不出现在视图里——只回「有没有」。"""
    values, secrets_set = settings_view(
        _SCHEMA,
        {"embedding_api_key": "sk-secret", "embedding_provider": "dashscope"},
        defaults={"embedding_api_key": "sk-from-env"},
    )

    assert "embedding_api_key" not in values
    assert "sk-secret" not in json.dumps(values)
    assert secrets_set == ["embedding_api_key"]


def test_settings_view_marks_secret_set_by_env_only() -> None:
    """`.env` 里配了 key（插件配置里没有）也算「已配置」，前端据此提示"留空则沿用"。"""
    values, secrets_set = settings_view(_SCHEMA, {}, defaults={"embedding_api_key": "sk-env"})

    assert secrets_set == ["embedding_api_key"]
    assert values == {}


def test_settings_view_keeps_unknown_saved_keys() -> None:
    """手改过配置文件的人不该因为打开一次界面就被抹掉数据（写回时会一起提交）。"""
    values, _ = settings_view(_SCHEMA, {"hand_edited": "keep-me"}, defaults={})

    assert values["hand_edited"] == "keep-me"


def test_settings_view_skips_empty_defaults() -> None:
    """宿主值为空（没配）就不回填：不然界面会显示一堆空串。"""
    values, _ = settings_view(_SCHEMA, {}, defaults={"embedding_provider": "", "embedding_dim": None})

    assert values == {}


# =============================================================
# 合并：PUT 时密钥怎么处理
# =============================================================


def test_merge_keeps_saved_secret_when_absent() -> None:
    """前端拿不到明文，密码框不会回填 → 没提交 = 用户没动它 = 沿用。"""
    merged = merge_secret_fields(_SCHEMA, {"embedding_api_key": "sk-old"}, {"embedding_provider": "x"})

    assert merged == {"embedding_provider": "x", "embedding_api_key": "sk-old"}


def test_merge_null_clears_secret() -> None:
    """显式 null = 清除（回落到 .env）——否则用户没有退路。"""
    merged = merge_secret_fields(_SCHEMA, {"embedding_api_key": "sk-old"}, {"embedding_api_key": None})

    assert "embedding_api_key" not in merged


def test_merge_blank_secret_keeps_saved() -> None:
    """空串等同「没填」（防御性）：只有显式 null 才是清除，避免误删 key。"""
    merged = merge_secret_fields(_SCHEMA, {"embedding_api_key": "sk-old"}, {"embedding_api_key": "  "})

    assert merged["embedding_api_key"] == "sk-old"


def test_merge_writes_new_secret_trimmed() -> None:
    merged = merge_secret_fields(_SCHEMA, {"embedding_api_key": "sk-old"}, {"embedding_api_key": " sk-new "})

    assert merged["embedding_api_key"] == "sk-new"


def test_merge_ignores_non_secret_fields() -> None:
    """非密钥字段原样透传（包括空值删除：清空路径 = 不配置）。"""
    merged = merge_secret_fields(_SCHEMA, {"embedding_provider": "dashscope"}, {"embedding_dim": 1024})

    assert merged == {"embedding_dim": 1024}


# =============================================================
# 白名单筛选
# =============================================================


def test_overridable_fields_filters_by_whitelist() -> None:
    assert overridable_fields(
        ["embedding_provider", "tavern_dir", "cors_origins", "gpt_sovits_base_url"]
    ) == ["embedding_provider", "gpt_sovits_base_url"]


@pytest.fixture(autouse=True)
def _clean_overrides():
    """覆盖层是进程级状态：每个用例前后清空，避免串味。"""
    clear_runtime_overrides()
    yield
    clear_runtime_overrides()
