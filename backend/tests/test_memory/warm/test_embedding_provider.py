"""真实 embedding provider 测试（httpx.MockTransport 模拟，无网络、无 key）。"""

import json
import math

import httpx
import pytest

from app.memory.warm.embedding import OpenAICompatibleEmbeddingProvider


def _mock_client(handler) -> httpx.Client:
    """构造注入 MockTransport 的 httpx 客户端（供 openai SDK 使用）。"""
    return httpx.Client(transport=httpx.MockTransport(handler))


def _embeddings_handler(dim: int = 3, *, reverse_order: bool = False):
    """返回 OpenAI 兼容的 embeddings 响应。

    向量取单位基向量 e_(i % dim)：它本身就是归一化的，便于同时校验
    「顺序正确」与「未被意外改动」。reverse_order 模拟服务端乱序返回。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        inputs = payload["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        data = []
        for i in range(len(inputs)):
            vector = [0.0] * dim
            vector[i % dim] = 1.0
            data.append({"object": "embedding", "index": i, "embedding": vector})
        if reverse_order:
            data.reverse()
        return httpx.Response(
            200,
            json={"object": "list", "data": data, "model": payload["model"]},
            request=request,
        )

    return handler


def _provider(handler, *, dim: int = 3) -> OpenAICompatibleEmbeddingProvider:
    return OpenAICompatibleEmbeddingProvider(
        name="siliconflow",
        api_key="test-key",
        model="Qwen/Qwen3-Embedding-8B",
        dimension=dim,
        http_client=_mock_client(handler),
    )


def test_embed_batch_uses_single_request() -> None:
    """批量编码只发一次请求（避免逐条 HTTP 往返）。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return _embeddings_handler()(request)

    provider = _provider(handler)
    vectors = provider.embed_batch(["甲", "乙", "丙"])

    assert len(calls) == 1, "批量编码不应拆成多次请求"
    assert calls[0]["input"] == ["甲", "乙", "丙"]
    assert len(vectors) == 3


def test_embed_batch_orders_by_index() -> None:
    """服务端乱序返回时，结果仍需与输入顺序一一对应。"""
    provider = _provider(_embeddings_handler(dim=2, reverse_order=True), dim=2)
    vectors = provider.embed_batch(["第一条", "第二条", "第三条"])

    # index i 的向量是 e_(i%2)，用「1.0 所在下标」校验顺序
    assert [v.index(1.0) for v in vectors] == [0, 1, 0]


def test_embed_single_delegates_to_batch() -> None:
    """单条编码内部走批量接口，行为一致。"""
    provider = _provider(_embeddings_handler(dim=2), dim=2)
    vector = provider.embed("只有一条")

    assert vector == [1.0, 0.0]


def test_output_is_l2_normalized() -> None:
    """端点返回未归一化向量时，provider 仍须输出归一化向量。

    cosine_similarity 是点积实现，全靠这条契约才等价于余弦相似度。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        inputs = payload["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        data = [
            {"object": "embedding", "index": i, "embedding": [3.0, 4.0]}
            for i in range(len(inputs))
        ]
        return httpx.Response(
            200,
            json={"object": "list", "data": data, "model": payload["model"]},
            request=request,
        )

    provider = _provider(handler, dim=2)
    vector = provider.embed("未归一化的向量")

    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)
    assert vector == pytest.approx([0.6, 0.8])


def test_embed_batch_empty_makes_no_request() -> None:
    """空输入直接返回空列表，不产生请求。"""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("空输入不应发起请求")

    provider = _provider(handler)
    assert provider.embed_batch([]) == []


def test_dimension_mismatch_raises_with_hint() -> None:
    """实际维度与配置不符时立即报错，并提示应填的配置值。"""
    provider = _provider(_embeddings_handler(dim=5), dim=1024)

    with pytest.raises(ValueError, match="EMBEDDING_DIM=5"):
        provider.embed("维度对不上")


def test_dimension_property_returns_configured_value() -> None:
    """dimension 由配置给定（Qdrant 建 collection 需要静态维度）。"""
    provider = _provider(_embeddings_handler(), dim=1024)
    assert provider.dimension == 1024


def test_requires_api_key() -> None:
    """缺 key 时构造即报错，而非拖到首次请求。"""
    with pytest.raises(ValueError, match="api_key"):
        OpenAICompatibleEmbeddingProvider(name="siliconflow", api_key="", model="m")


def test_requires_model() -> None:
    """缺模型名时构造即报错。"""
    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleEmbeddingProvider(name="siliconflow", api_key="k", model="")


def test_generic_provider_requires_base_url() -> None:
    """openai-compatible 无默认端点，必须显式给 base_url。"""
    with pytest.raises(ValueError, match="base_url"):
        OpenAICompatibleEmbeddingProvider(
            name="openai-compatible", api_key="k", model="m"
        )


def test_request_payload_shape() -> None:
    """请求体应含 model 与 input，且发往 /embeddings。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _embeddings_handler()(request)

    provider = _provider(handler)
    provider.embed("我最近很累")

    assert captured["url"].endswith("/embeddings")
    assert captured["body"]["model"] == "Qwen/Qwen3-Embedding-8B"
    assert captured["body"]["input"] == ["我最近很累"]
