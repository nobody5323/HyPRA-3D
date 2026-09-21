"""BM25 索引测试。"""

import pytest

from app.rag.retrieval.bm25 import BM25Index


@pytest.fixture()
def index() -> BM25Index:
    idx = BM25Index()
    idx.add("m1", "小林说他最近失眠，晚上总是睡不着")
    idx.add("m2", "小林喜欢下雨天，听着雨声很放松")
    idx.add("m3", "今天在咖啡馆遇到了老朋友")
    return idx


def test_search_hits_relevant_doc(index: BM25Index) -> None:
    hits = dict(index.search("失眠睡不着", top_k=3))
    assert "m1" in hits
    assert "m3" not in hits          # 一个查询词都没命中 → 不返回


def test_unmatched_query_returns_empty(index: BM25Index) -> None:
    """稀疏通道宁缺毋滥：完全不命中时返回空列表。"""
    assert index.search("量子力学与广义相对论", top_k=3) == []


def test_query_without_tokens_returns_empty(index: BM25Index) -> None:
    assert index.search("", top_k=3) == []
    assert index.search("，。！？", top_k=3) == []


def test_incremental_add_and_remove(index: BM25Index) -> None:
    index.add("m4", "领导又加了三个需求，压力很大")
    assert "m4" in index
    assert len(index) == 4

    assert index.remove("m4") is True
    assert "m4" not in index
    assert len(index) == 3
    assert index.remove("m4") is False        # 重复删除返回 False


def test_replace_same_id_does_not_duplicate() -> None:
    """同一 doc_id 再次 add 应覆盖，而非累积。"""
    idx = BM25Index()
    idx.add("m1", "小林喜欢猫")
    idx.add("m1", "小林喜欢狗")

    assert len(idx) == 1
    assert dict(idx.search("狗", top_k=1))["m1"] > 0
    assert idx.search("猫", top_k=1) == []


def test_rare_term_scores_higher_than_common_term() -> None:
    """IDF 生效：只出现在一个文档的词，得分高于出现在所有文档的词。"""
    idx = BM25Index()
    idx.add("a", "小林 今天 心情 不错")
    idx.add("b", "小林 今天 有点 失眠")
    idx.add("c", "小林 今天 工作 很忙")

    rare = dict(idx.search("失眠", top_k=3))        # 仅 b 含此词
    common = dict(idx.search("小林", top_k=3))       # 全部文档含此词

    assert "b" in rare
    assert len(common) == 3
    assert rare["b"] > common["b"]


def test_empty_text_not_indexed() -> None:
    idx = BM25Index()
    idx.add("m1", "，。！")
    assert len(idx) == 0
    assert "m1" not in idx


def test_top_k_limits_results(index: BM25Index) -> None:
    assert len(index.search("小林", top_k=1)) == 1
    assert len(index.search("小林", top_k=10)) == 2   # 只有 m1/m2 含「小林」


def test_clear_empties_index(index: BM25Index) -> None:
    index.clear()
    assert len(index) == 0
    assert index.search("小林", top_k=3) == []
    assert index.avg_length == 0.0


def test_doc_ids_snapshot(index: BM25Index) -> None:
    assert index.doc_ids() == {"m1", "m2", "m3"}
