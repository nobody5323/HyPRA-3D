"""配置层测试。

注意：默认值测试必须用 `_env_file=None` 隔离本地 backend/.env——
否则「代码默认值」的断言会被开发者本地配置（如真实 LLM provider）破坏，
导致测试结果随环境漂移。
"""

from app.config import Settings


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
