"""Embedding 提供者抽象 + 真实云实现 + 确定性本地实现。

- EmbeddingProvider：接口，供 store / 世界书索引编码文本；
- OpenAICompatibleEmbeddingProvider：真实实现（openai SDK），覆盖
  dashscope / siliconflow / openai-compatible 等兼容端点；
- DeterministicEmbeddingProvider：纯本地确定性编码（字符 n-gram 特征哈希），
  用于离线开发、测试与 CI——同文本必得同向量，共享字词的文本向量相近，
  足以驱动 warm 层全链路测试；不产生任何网络请求与费用。

注意：两种 provider 的**相似度分布完全不同**（确定性实现实测落在 0.0–0.2，
真实 embedding 常在 0.4–0.9），因此依赖阈值的向量触发（如世界书
vector_threshold）必须按当前 provider 调参，不能套用同一个值。
"""

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections import Counter

from openai import OpenAI

_WS = re.compile(r"\s+")


def _l2_normalize(vector: list[float]) -> list[float]:
    """L2 归一化（零向量原样返回）。

    余弦相似度在本地是**点积实现**（见 cosine_similarity），因此每个 provider
    都必须保证输出已归一化。云端端点并不一定归一化（实测硅基流动的
    Qwen3-Embedding-8B 返回范数 1.0，但其他端点未必），故在此显式归一化——
    对已归一化的向量是幂等操作。
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]

#: 未显式配置时使用的向量维度兜底值
DEFAULT_EMBEDDING_DIM = 1024

#: 各提供商默认 base_url（可被 .env 的 EMBEDDING_BASE_URL 覆盖）
DEFAULT_BASE_URLS: dict[str, str] = {
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}

#: 走 OpenAI 兼容实现（openai SDK）的 provider 名
OPENAI_COMPATIBLE_NAMES = frozenset({"dashscope", "siliconflow", "openai-compatible"})

#: 本地确定性实现的别名
DETERMINISTIC_NAMES = frozenset({"deterministic", "memory", "mock"})


class EmbeddingProvider(ABC):
    """文本编码抽象。"""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """把一段文本编码为向量。"""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量编码，默认逐条调用 embed。

        真实 provider 应重写为**单次批量请求**：世界书索引构造时要一次性编码
        多个条目的语义文本，逐条发 HTTP 请求会显著变慢。
        约定：返回列表与输入等长且顺序一一对应。
        """
        return [self.embed(text) for text in texts]

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度。"""


class DeterministicEmbeddingProvider(EmbeddingProvider):
    """确定性本地 embedding（n-gram 特征哈希 + L2 归一化）。

    实现要点：
    - 字符 uni-gram + bi-gram 作为特征；
    - 过滤中文高频虚字（的了是很…）——这类字几乎出现在所有句子中，
      会污染相似度（导致无关文本也相近）；
    - md5 哈希到高维桶（默认 256），短文本区分度更好。
    仅用于离线开发/测试/CI，不产生网络请求；真实现接入时替换。
    """

    # 中文高频虚字 / 代词语助词（过滤后特征聚焦实义字词）
    _STOP_CHARS = set(
        "的了是很在我有和就不人都一这中上个也还那要会没她他它吗吧啊哦呀呢么与你"
    )

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def _features(self, text: str) -> list[str]:
        """字符 uni-gram + bi-gram 特征（去空白、去虚字后按字符切分）。"""
        chars = [ch for ch in _WS.sub("", text) if ch not in self._STOP_CHARS]
        grams = list(chars)
        grams += [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
        return grams

    @staticmethod
    def _hash_to_bucket(token: str, dim: int) -> int:
        """md5 哈希 → 桶下标（确定性、分布均匀）。"""
        digest = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") % dim

    def embed(self, text: str) -> list[float]:
        counts = Counter(self._features(text))
        vec = [0.0] * self._dim
        for token, freq in counts.items():
            vec[self._hash_to_bucket(token, self._dim)] += freq
        return _l2_normalize(vec)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """两个向量的余弦相似度。

    **前提：两个向量都已 L2 归一化**（此时余弦等于点积，省去重复算范数）。
    归一化由各 provider 的 embed/embed_batch 保证（见 _l2_normalize）。
    传入未归一化向量会得到内积而非余弦（数值偏大，相似度判断失准）。
    """
    if len(a) != len(b):
        raise ValueError(f"向量维度不一致：{len(a)} vs {len(b)}")
    return sum(x * y for x, y in zip(a, b))


def create_embedding_provider(
    provider: str = "",
    *,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    dimension: int = DEFAULT_EMBEDDING_DIM,
    timeout: float = 30.0,
    http_client=None,
) -> EmbeddingProvider:
    """按配置创建 embedding provider。

    参数:
        provider: deterministic（默认，零依赖）| dashscope | siliconflow
            | openai-compatible；
        api_key / model / base_url: 云端接入参数（deterministic 忽略）；
        dimension: 向量维度（Qdrant 建 collection 需要静态值，不能自动探测）；
        timeout: 单次请求超时（秒）；
        http_client: 自定义 httpx 客户端（测试注入用）。
    """
    name = (provider or "deterministic").strip().lower()
    if not name or name in DETERMINISTIC_NAMES:
        return DeterministicEmbeddingProvider()
    if name in OPENAI_COMPATIBLE_NAMES:
        return OpenAICompatibleEmbeddingProvider(
            name=name,
            api_key=api_key,
            model=model,
            base_url=base_url or None,
            dimension=dimension,
            timeout=timeout,
            http_client=http_client,
        )
    raise ValueError(f"未知 embedding provider：{provider!r}")


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """通过 openai SDK 调用任意 OpenAI 兼容 embeddings 端点。

    覆盖国内主流托管 API：
    - dashscope          阿里百炼（text-embedding-v3 等）
    - siliconflow        硅基流动（BAAI/bge-m3、Qwen/Qwen3-Embedding-* 等）
    - openai-compatible  任意兼容端点（需自填 EMBEDDING_BASE_URL）

    设计要点：
    - 依赖注入 http_client，便于用 httpx.MockTransport 做**无网络测试**；
    - base_url 未显式提供时按 provider 名取默认值；
    - embed_batch 走**单次批量请求**（embeddings 接口原生支持数组入参），
      避免逐条发请求；响应按 index 排序，保证与输入顺序一一对应；
    - dimension 由构造参数给定，不在首次请求后才确定——Qdrant 建 collection
      需要静态维度（见 qdrant_store._ensure_collection）。首次拿到向量时
      会校验实际维度与配置是否一致，不一致立即报错并提示如何修正。
    """

    def __init__(
        self,
        *,
        name: str = "openai-compatible",
        api_key: str,
        model: str,
        base_url: str | None = None,
        dimension: int = DEFAULT_EMBEDDING_DIM,
        timeout: float = 30.0,
        max_retries: int = 2,
        http_client=None,
    ) -> None:
        """初始化。

        参数:
            name: 提供商名（dashscope / siliconflow / openai-compatible）；
            api_key: 云端 key（来自 .env）；
            model: embedding 模型名，如 text-embedding-v3；
            base_url: 端点地址，缺省按 name 取默认值；
            dimension: 期望的向量维度（须与模型实际输出一致）；
            timeout / max_retries: 网络超时与重试（SDK 自带退避重试）；
            http_client: 自定义 httpx 客户端（测试注入用）。
        """
        resolved_base = base_url or DEFAULT_BASE_URLS.get(name, "")
        if not resolved_base:
            raise ValueError(
                f"provider「{name}」需要显式 base_url（请设置 EMBEDDING_BASE_URL）"
            )
        if not api_key:
            raise ValueError(f"provider「{name}」缺少 api_key（请设置 EMBEDDING_API_KEY）")
        if not model:
            raise ValueError(f"provider「{name}」缺少 model（请设置 EMBEDDING_MODEL）")

        self.name = name
        self.model = model
        self._dimension = dimension
        self._client = OpenAI(
            api_key=api_key,
            base_url=resolved_base,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        """编码单条文本（内部走批量接口，保证行为一致）。"""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """单次请求编码多条文本，返回顺序与输入一一对应。"""
        if not texts:
            return []
        response = self._client.embeddings.create(model=self.model, input=texts)
        # 按 index 排序：服务端不保证返回顺序与入参一致
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors = [
            _l2_normalize([float(value) for value in item.embedding])
            for item in ordered
        ]
        if len(vectors) != len(texts):
            raise ValueError(
                f"embedding 返回条数不符：期望 {len(texts)}，实际 {len(vectors)}"
            )
        actual_dim = len(vectors[0])
        if actual_dim != self._dimension:
            raise ValueError(
                f"embedding 维度与配置不符：模型 {self.model} 实际输出 {actual_dim}，"
                f"配置为 {self._dimension}。请设置 EMBEDDING_DIM={actual_dim} "
                "（Qdrant collection 维度必须与之一致；若已建库需删除旧 collection）。"
            )
        return vectors
