"""配置层测试。

注意：默认值测试必须用 `_env_file=None` 隔离本地 backend/.env——
否则「代码默认值」的断言会被开发者本地配置（如真实 LLM provider）破坏，
导致测试结果随环境漂移。
"""

from app.config import (
    RUNTIME_OVERRIDABLE_FIELDS,
    Settings,
    clear_runtime_overrides,
    replace_runtime_overrides,
    runtime_overrides,
)
from app.paths import data_root


def test_defaults_zero_dependency() -> None:
    """代码默认值应零依赖可跑：mock LLM + 本地确定性 embedding + memory 向量后端。"""
    settings = Settings(_env_file=None)  # 忽略本地 .env，验证代码默认值
    assert settings.llm_provider == "mock"
    assert settings.embedding_provider == "deterministic"
    assert settings.warm_backend == "memory"
    assert settings.qdrant_api_key == ""
    assert settings.memory_extractor == "rule"


def test_defaults_budgets() -> None:
    """PromptManager 预算默认值。"""
    settings = Settings(_env_file=None)
    assert settings.worldbook_budget > 0
    assert settings.memory_layer_budget > 0
    assert settings.prompt_history_budget > 0
    assert settings.prompt_total_budget > 0


def test_defaults_memory_recall_params() -> None:
    """记忆召回参数默认值（时间衰减与情绪加权均为可调项）。"""
    settings = Settings(_env_file=None)
    assert settings.memory_half_life_days > 0
    assert settings.memory_decay_exponent >= 0
    assert settings.memory_emotion_boost >= 1.0


def test_env_var_override(monkeypatch) -> None:
    """环境变量应能覆盖默认值（优先级高于 .env）。"""
    monkeypatch.setenv("LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("QDRANT_URL", "https://cloud.example.com:6333")

    settings = Settings(_env_file=None)
    assert settings.llm_provider == "dashscope"
    assert settings.qdrant_url == "https://cloud.example.com:6333"


def test_env_file_is_read_when_present(tmp_path, monkeypatch) -> None:
    """显式指定 .env 时应能读到其中配置。"""
    # 环境变量优先级高于 .env 文件（pydantic-settings 的既定行为）。
    # 全局 conftest 为了隔离本机 .env 会把 LLM_* 固定成 mock，
    # 而本用例验证的正是「文件能被读到」，因此先把这几个环境变量清掉。
    for name in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text("LLM_PROVIDER=siliconflow\nLLM_MODEL=test-model\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)
    assert settings.llm_provider == "siliconflow"
    assert settings.llm_model == "test-model"


# =============================================================
# 运行时配置覆盖层（界面设置 > .env）
# =============================================================


def test_runtime_override_wins_over_env(monkeypatch) -> None:
    """覆盖层的优先级高于环境变量与 .env（与对话模型的 runtime > env 一致）。"""
    monkeypatch.setenv("EMBEDDING_MODEL", "env-model")
    assert replace_runtime_overrides({"embedding_model": "ui-model"}) is True

    assert Settings(_env_file=None).embedding_model == "ui-model"
    assert runtime_overrides() == {"embedding_model": "ui-model"}


def test_runtime_override_absent_field_falls_back(monkeypatch) -> None:
    """没被覆盖的字段照旧读 .env / 环境变量（留空 = 沿用部署配置）。"""
    monkeypatch.setenv("EMBEDDING_MODEL", "env-model")
    replace_runtime_overrides({"embedding_base_url": "https://x/v1"})

    settings = Settings(_env_file=None)
    assert settings.embedding_model == "env-model"
    assert settings.embedding_base_url == "https://x/v1"


def test_runtime_override_rejects_non_whitelisted_field() -> None:
    """白名单外的键一律忽略：settings.json 是用户可手改的文件，不能改写任意宿主配置。"""
    assert replace_runtime_overrides({"cors_origins": "*", "llm_api_key": "leak"}) is False
    assert runtime_overrides() == {}
    assert Settings(_env_file=None).cors_origins != "*"


def test_runtime_override_coerces_types() -> None:
    """界面值是 JSON：数字/布尔可能是字符串，必须按字段类型转换（setattr 绕过 pydantic 校验）。"""
    replace_runtime_overrides(
        {"embedding_dim": "768", "gpt_sovits_speed": "1.2", "gpt_sovits_warmup": "false"}
    )

    settings = Settings(_env_file=None)
    assert settings.embedding_dim == 768
    assert settings.gpt_sovits_speed == 1.2
    assert settings.gpt_sovits_warmup is False


def test_runtime_override_ignores_unparsable_value() -> None:
    """无法转换的值忽略（回落 .env），而不是抛错让宿主起不来。"""
    assert replace_runtime_overrides({"embedding_dim": "很大"}) is False
    assert Settings(_env_file=None).embedding_dim == 1024


def test_runtime_override_resolves_path_fields() -> None:
    """覆盖的相对路径仍按 data_root() 解析——覆盖必须发生在路径解析之前。"""
    replace_runtime_overrides({"gpt_sovits_voices_file": "data/my_voices.json"})

    assert Settings(_env_file=None).gpt_sovits_voices_file == str(
        data_root() / "data" / "my_voices.json"
    )


def test_replace_reports_change_and_clears() -> None:
    """replace 的返回值是「是否真的变了」：调用方据此决定要不要重建单例。"""
    assert replace_runtime_overrides({"embedding_model": "a"}) is True
    assert replace_runtime_overrides({"embedding_model": "a"}) is False  # 幂等：不触发重建
    assert replace_runtime_overrides({"embedding_model": "b"}) is True
    # 全量替换：上一轮多出来的键不会残留（插件被禁用时正是这个语义）
    assert replace_runtime_overrides({}) is True
    assert runtime_overrides() == {}

    replace_runtime_overrides({"embedding_model": "a"})
    assert clear_runtime_overrides() is True
    assert clear_runtime_overrides() is False
    assert Settings(_env_file=None).embedding_model == "text-embedding-v3"


def test_whitelist_fields_exist_in_settings() -> None:
    """白名单里的字段必须真实存在：写错字段名只会静静地不生效。"""
    unknown = RUNTIME_OVERRIDABLE_FIELDS - set(Settings.model_fields)
    assert unknown == set()
