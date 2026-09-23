"""分块测试。

语义分块用**假 embed**（按预设向量表返回）验证，不依赖真实模型——
要验证的是「相似度低谷处是否提前切分」这个机制，而不是模型质量。
"""

import pytest

from app.memory.knowledge.chunker import create_chunker, split_text


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


# ---------- 策略装配（拆分新增：分块器可替换）----------


def _topic_embed(units: list[str]) -> list[list[float]]:
    """假编码器：把「话题 A / 其他」映射到正交单位向量。

    于是相邻单元同话题时相似度为 1、跨话题时为 0——语义低谷位置完全可控。
    """
    return [[1.0, 0.0] if "话题A" in unit else [0.0, 1.0] for unit in units]


TOPIC_TEXT = "\n\n".join(["话题A的内容"] * 3 + ["话题B的内容"] * 3)


def test_plain_chunker_ignores_semantics() -> None:
    """纯长度策略没有语义低谷：没到 target 就不切。"""
    chunks = create_chunker("plain").split(
        TOPIC_TEXT, target=1000, max_chars=1000, overlap=0, min_chunk=1
    )

    assert len(chunks) == 1


def test_semantic_chunker_cuts_at_topic_shift() -> None:
    """语义策略在话题转折处提前切，哪怕长度远未到 target。"""
    chunks = create_chunker("semantic", embed=_topic_embed).split(
        TOPIC_TEXT, target=1000, max_chars=1000, overlap=0, min_chunk=1
    )

    assert len(chunks) == 2
    assert "话题A" in chunks[0] and "话题B" not in chunks[0]
    assert "话题B" in chunks[1]


def test_semantic_chunker_requires_embedding() -> None:
    """显式要求语义切分却没给编码函数 → 报错，而不是静默退成纯递归。

    悄悄降级会让分块质量莫名变差，比启动时明确报错难查得多（与 tokenizer 的
    强制模式同理）。
    """
    with pytest.raises(ValueError, match="需要 embed 函数"):
        create_chunker("semantic")


def test_auto_follows_embedding_availability() -> None:
    """auto 跟随「有没有 embed」——这就是既有 split_text 的降级语义。"""
    assert create_chunker("auto").name == "plain"
    assert create_chunker("").name == "plain"
    assert create_chunker("auto", embed=_topic_embed).name == "semantic"


def test_plain_chunker_is_reproducible() -> None:
    """纯递归的切点可复现——换 embedding 模型不会改变分块。

    这正是它除了「零依赖降级」之外的另一项价值：语义切点随模型变化，
    缓存与增量索引会整体失配。
    """
    chunker = create_chunker("plain")
    first = chunker.split(TOPIC_TEXT, target=20, max_chars=40, overlap=0, min_chunk=1)
    second = chunker.split(TOPIC_TEXT, target=20, max_chars=40, overlap=0, min_chunk=1)

    assert first == second
    assert len(first) > 1


def test_unknown_chunker_raises() -> None:
    with pytest.raises(ValueError, match="未知分块器"):
        create_chunker("by-topic")


def test_semantic_boundaries_tolerates_length_mismatch() -> None:
    """编码结果与输入不等长时宁可退化为纯长度切分，也不按错位的向量切。"""
    from app.memory.knowledge.chunker import semantic_boundaries

    units = ["甲", "乙", "丙"]

    assert semantic_boundaries(units, lambda _: [[1.0, 0.0]], 0.75) == set()
    assert semantic_boundaries(["只有一个"], _topic_embed, 0.75) == set()
