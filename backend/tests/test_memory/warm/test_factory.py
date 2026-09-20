"""Embedding 工厂测试（全部离线：仅验证实现选择与参数校验，不发请求）。"""

import pytest

from app.memory.warm.embedding import (
    DeterministicEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    create_embedding_provider,
)


def test_create_default_deterministic() -> None:
    provider = create_embedding_provider("")
    assert isinstance(provider, DeterministicEmbeddingProvider)


def test_create_aliases() -> None:
    for name in ("deterministic", "memory", "mock"):
        provider = create_embedding_provider(name)
        assert isinstance(provider, DeterministicEmbeddingProvider)


def test_create_unknown_raises() -> None:
    with pytest.raises(ValueError):
        create_embedding_provider("weird-provider")


def test_create_cloud_with_full_config() -> None:
    """云端 provider 参数齐全时应成功创建（不发请求）。"""
    provider = create_embedding_provider(
        "siliconflow",
        api_key="test-key",
        model="BAAI/bge-m3",
        dimension=1024,
    )
    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.dimension == 1024


def test_create_cloud_requires_api_key() -> None:
    """缺 key 时立即报错（而不是等到首次对话）。"""
    for name in ("dashscope", "siliconflow"):
        with pytest.raises(ValueError, match="api_key"):
            create_embedding_provider(name, model="m")


def test_create_generic_requires_base_url() -> None:
    """openai-compatible 无默认端点，必须显式给 base_url。"""
    with pytest.raises(ValueError, match="base_url"):
        create_embedding_provider("openai-compatible", api_key="k", model="m")
