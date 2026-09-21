"""分块测试。

语义分块用**假 embed**（按预设向量表返回）验证，不依赖真实模型——
要验证的是「相似度低谷处是否提前切分」这个机制，而不是模型质量。
"""

import pytest

from app.memory.knowledge.chunker import split_text


def _fake_embed(mapping: dict[str, list[float]]):
    """按文本返回预设向量的假编码器（未登记的文本返回零向量）。"""

    def embed(texts: list[str]) -> list[list[float]]:
        return [mapping.get(text, [0.0, 0.0, 1.0]) for text in texts]

    return embed


# ---------- 基本行为 ----------


def test_empty_text_returns_no_chunks() -> None:
    assert split_text("") == []
    assert split_text("   \n  ") == []


def test_short_text_is_single_chunk() -> None:
    text = "小林喜欢下雨天。"
    assert split_text(text) == [text]


def test_long_text_splits_into_multiple_chunks() -> None:
    paragraph = "苏澄的咨询室在老城区一栋安静的二层小楼里，浅米色墙面，墨绿沙发。" * 15
    chunks = split_text(paragraph, target=200, max_chars=300, overlap=0)

    assert len(chunks) > 1
    assert all(len(chunk) <= 300 + 40 for chunk in chunks)   # 容差：单句可能略超


def test_markdown_heading_starts_new_chunk() -> None:
    text = "# 第一章\n" + "甲" * 150 + "\n# 第二章\n" + "乙" * 150
    chunks = split_text(text, target=100, max_chars=200, overlap=0, min_chunk=10)

    assert any(chunk.startswith("# 第一章") for chunk in chunks)
    assert any(chunk.startswith("# 第二章") for chunk in chunks)
    # 两章不应被塞进同一块
    assert len([c for c in chunks if c.startswith("#")]) == 2


def test_invalid_params_raise() -> None:
    with pytest.raises(ValueError):
        split_text("内容", max_chars=0)
    with pytest.raises(ValueError):
        split_text("内容", target=0)


# ---------- overlap ----------


def test_overlap_carries_tail_of_previous_chunk() -> None:
    """下一块开头应包含上一块尾部的完整句子。"""
    text = "".join(f"这是第{i}句话，用来撑长文本。" for i in range(20))
    chunks = split_text(text, target=60, max_chars=90, overlap=30, min_chunk=10)

    assert len(chunks) >= 2
    previous_tail = chunks[0][-10:]
    assert previous_tail in chunks[1]


def test_zero_overlap_keeps_chunks_disjoint() -> None:
    text = "".join(f"第{i}句内容。".replace("i", str(i)) for i in range(30))
    chunks = split_text(text, target=60, max_chars=90, overlap=0, min_chunk=10)

    for previous, current in zip(chunks, chunks[1:]):
        assert not current.startswith(previous[-20:])


# ---------- 语义边界 ----------


def test_semantic_valley_forces_earlier_split() -> None:
    """相邻单元语义差距大时，即便没到 target 也应提前断开。"""
    unit_a = "甲" * 100
    unit_b = "乙" * 100
    text = f"{unit_a}\n\n{unit_b}"

    embed = _fake_embed({unit_a: [1.0, 0.0, 0.0], unit_b: [0.0, 1.0, 0.0]})
    chunks = split_text(
        text, target=500, max_chars=300, overlap=0, min_chunk=10, embed=embed
    )

    assert len(chunks) == 2            # 语义低谷处切开（否则会合成一块）


def test_similar_units_stay_together() -> None:
    """语义相近的单元应合并成一块，不应被切碎。"""
    unit_a = "甲" * 100
    unit_b = "乙" * 100
    text = f"{unit_a}\n\n{unit_b}"

    embed = _fake_embed({unit_a: [1.0, 0.0, 0.0], unit_b: [1.0, 0.0, 0.0]})
    chunks = split_text(
        text, target=500, max_chars=300, overlap=0, min_chunk=10, embed=embed
    )

    assert len(chunks) == 1


def test_without_embed_falls_back_to_recursive_only() -> None:
    """无编码器时退化为纯递归切分：不报错，只是少了语义判据。"""
    paragraph = "这是一段很长的文本，用来测试降级行为。" * 10
    chunks = split_text(paragraph, target=100, max_chars=200, overlap=0)

    assert chunks
    assert all(chunk.strip() for chunk in chunks)


# ---------- 长度约束 ----------


def test_oversized_single_sentence_is_hard_split() -> None:
    """没有标点的超长串必须硬切，不能产生超过上限的块。"""
    text = "字" * 1000
    chunks = split_text(text, target=200, max_chars=300, overlap=0, min_chunk=10)

    assert len(chunks) >= 4
    assert all(len(chunk) <= 300 for chunk in chunks)


def test_fragments_are_merged() -> None:
    """过短的碎片应被并入相邻单元，而不是各自成块。"""
    text = "短。\n\n" + "这是较长的一段正文内容。" * 10
    chunks = split_text(text, target=200, max_chars=300, overlap=0, min_chunk=60)

    assert all(len(chunk) >= 60 for chunk in chunks)
