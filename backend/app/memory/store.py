"""记忆门面：聚合分层记忆，按固定顺序产出记忆上下文。

本门面负责「世界书之后、滚动窗口之前」这一段：
    世界书触发 > **情景记忆召回 > 语义事实** > 滚动窗口

分层职责：
- 工作记忆：会话滚动窗口 —— 由 session.ChatTurn 承载，不经本门面；
- 情景记忆：warm store，**混合检索**（BM25 + 稠密向量 → RRF 融合，
  候选按纯相似度选取）后再施加时间衰减；
- 语义记忆：cold store 的结构化事实（importance 优先）。

容错：任一层不可用（Qdrant 未启动 / jieba 缺失 / BM25 索引构建失败）时记
warning 并降级，不阻断对话；稀疏通道不可用时自动退化为纯向量召回。
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.memory.cold.extractor import RuleBasedExtractor, TurnExtractor
from app.memory.cold.models import Fact, Summary
from app.memory.cold.store import ColdMemoryStore
from app.memory.warm.base import MemoryRecord, SearchResult, WarmMemoryStore
from app.memory.warm.decay import combined_score
from app.rag.retrieval.bm25 import BM25Index
from app.rag.retrieval.hybrid import reciprocal_rank_fusion

# 默认参数（均可经 config 覆盖）
DEFAULT_FACT_LIMIT = 5            # 语义事实召回条数
DEFAULT_MEMORY_TOP_K = 3          # 情景记忆最终召回条数
DEFAULT_CANDIDATE_N = 10          # 混合检索各通道候选数（应远大于 final top_k）
DEFAULT_RRF_K = 60                # RRF 平滑常数
DEFAULT_MIN_SIMILARITY = 0.0      # 稠密通道相似度阈值（融合前过滤，0=不过滤）
DEFAULT_HALF_LIFE_DAYS = 30.0     # 时间衰减半衰期（天）
DEFAULT_DECAY_EXPONENT = 1.0      # 衰减强度（0=不衰减，>1=更强地让位于近期）
DEFAULT_EMOTION_BOOST = 1.25      # 同情绪记忆的召回分加权系数（参照⑤）


@dataclass
class MemoryContext:
    """一次记忆召回的结果（三层聚合）。"""

    facts: list[Fact] = field(default_factory=list)
    memories: list[SearchResult] = field(default_factory=list)
    summary: Summary | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """三层皆无有效内容。"""
        has_summary = bool(self.summary and self.summary.content.strip())
        return not (self.facts or self.memories or has_summary)


@dataclass
class _LexicalView:
    """某陪伴对象的稀疏检索视图（BM25 索引 + 记录快照）。

    两者必须同步维护：BM25 命中但不在快照里的文档拿不到 created_at，
    也就无法参与时间衰减，会被静默丢弃。
    """

    index: BM25Index
    records: dict[str, MemoryRecord]


class MemoryStore:
    """三层记忆门面（召回 + 写入）。"""

    def __init__(
        self,
        cold: ColdMemoryStore,
        warm: WarmMemoryStore,
        *,
        fact_limit: int = DEFAULT_FACT_LIMIT,
        memory_top_k: int = DEFAULT_MEMORY_TOP_K,
        hybrid_enabled: bool = True,
        candidate_n: int = DEFAULT_CANDIDATE_N,
        rrf_k: int = DEFAULT_RRF_K,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
        decay_exponent: float = DEFAULT_DECAY_EXPONENT,
        emotion_boost: float = DEFAULT_EMOTION_BOOST,
        extractor: TurnExtractor | None = None,
    ) -> None:
        self.cold = cold
        self.warm = warm
        self.fact_limit = fact_limit
        self.memory_top_k = memory_top_k
        self.hybrid_enabled = hybrid_enabled
        self.candidate_n = candidate_n
        self.rrf_k = rrf_k
        self.min_similarity = min_similarity
        self.half_life_days = half_life_days
        self.decay_exponent = decay_exponent
        self.emotion_boost = emotion_boost
        self.extractor = extractor or RuleBasedExtractor()
        # 稀疏（BM25）索引按陪伴对象惰性构建并缓存：语料是全量读取的，
        # 不能每轮重建
        self._lexical: dict[str, _LexicalView] = {}

    def recall(
        self,
        companion_id: str,
        query: str,
        *,
        emotion: str | None = None,
    ) -> MemoryContext:
        """按 query 召回三层记忆（任一层异常降级为 warning）。

        参数:
            emotion: 当前情绪标签（英文）；提供时对同情绪记忆加权（参照⑤），
                实现「此刻焦虑 → 更易想起过往焦虑相关的片段」。
        """
        warnings: list[str] = []
        memories: list[SearchResult] = []
        facts: list[Fact] = []
        summary: Summary | None = None

        # ① 情景记忆混合召回（BM25 + 向量 → RRF → 时间衰减）
        try:
            memories = self._recall_episodic(companion_id, query, warnings)
        except Exception as exc:  # 存储不可用不应阻断对话
            warnings.append(f"情景记忆召回失败（已降级）：{exc}")

        # ② 冷层事实 + ③ 摘要
        try:
            facts = self.cold.list_facts(companion_id, limit=self.fact_limit)
            # importance 优先，其次按时间新近
            facts.sort(key=lambda f: (f.importance, f.created_at), reverse=True)
        except Exception as exc:
            warnings.append(f"冷层事实读取失败（已降级）：{exc}")

        try:
            summary = self.cold.get_summary(companion_id)
        except Exception as exc:
            warnings.append(f"冷层摘要读取失败（已降级）：{exc}")

        # ④ 情绪加权（参照⑤）
        if emotion:
            memories = self._boost_memories_by_emotion(memories, emotion)
            facts = self._boost_facts_by_emotion(facts, emotion)

        return MemoryContext(
            facts=facts,
            memories=memories,
            summary=summary,
            warnings=warnings,
        )

    def _boost_memories_by_emotion(
        self, memories: list[SearchResult], emotion: str
    ) -> list[SearchResult]:
        """温层：同情绪记忆提升分数并重排（加权系数可配）。"""
        for item in memories:
            if item.record.metadata.get("emotion") == emotion:
                item.score *= self.emotion_boost
        memories.sort(key=lambda r: r.score, reverse=True)
        return memories

    def _lexical_view(self, companion_id: str) -> _LexicalView:
        """惰性构建该陪伴对象的稀疏检索视图（首次召回时全量拉取并建索引）。"""
        view = self._lexical.get(companion_id)
        if view is None:
            records = self.warm.list_records(companion_id)
            index = BM25Index()
            for record in records:
                index.add(record.memory_id, record.text)
            view = _LexicalView(
                index=index,
                records={record.memory_id: record for record in records},
            )
            self._lexical[companion_id] = view
        return view

    def _sync_lexical(self, companion_id: str, memory_id: str, text: str) -> None:
        """把新写入的记忆同步进稀疏索引（未构建则留待惰性构建，不做额外开销）。"""
        view = self._lexical.get(companion_id)
        if view is None:
            return
        view.index.add(memory_id, text)
        view.records[memory_id] = MemoryRecord(
            memory_id=memory_id,
            companion_id=companion_id,
            text=text,
            created_at=datetime.now(),
        )

    def _recall_episodic(
        self,
        companion_id: str,
        query: str,
        warnings: list[str],
    ) -> list[SearchResult]:
        """情景记忆混合召回：稠密 + BM25 → RRF → 时间衰减 → top_k。

        两处关键顺序：
        1. **相似度阈值在融合前生效**：RRF 分数只有相对含义，融合后无法设阈值；
        2. **时间衰减在融合后生效**：先取纯相似度候选，避免「先衰减再截断」
           让旧记忆在进入融合前就被滤掉。
        """
        # ① 稠密通道（decay_exponent=0 即不衰减，只按相似度取候选）
        dense = self.warm.search(
            companion_id,
            query,
            top_k=self.candidate_n,
            decay_exponent=0.0,
        )
        dense = [item for item in dense if item.raw_similarity >= self.min_similarity]

        rankings: list[list[str]] = [[item.record.memory_id for item in dense]]
        records: dict[str, MemoryRecord] = {
            item.record.memory_id: item.record for item in dense
        }
        similarity_by_id = {
            item.record.memory_id: item.raw_similarity for item in dense
        }

        # ② 稀疏通道（BM25）：BM25 能召回向量漏掉的文档，也能让老记忆
        #    因关键词命中而重新出现（对抗衰减与淘汰的正反馈）
        if self.hybrid_enabled:
            try:
                view = self._lexical_view(companion_id)
                sparse = view.index.search(query, top_k=self.candidate_n)
            except Exception as exc:  # noqa: BLE001 - 稀疏通道不可用不影响主链路
                warnings.append(f"BM25 检索不可用（本轮退化为纯向量召回）：{exc}")
                sparse = []
            if sparse:
                rankings.append([doc_id for doc_id, _ in sparse])
                for doc_id, _score in sparse:
                    record = view.records.get(doc_id)
                    if record is not None:
                        records.setdefault(doc_id, record)

        # ③ RRF 融合（只传稠密一路时，分数即 1/(k+rank)，保留相似度排序）
        fused = reciprocal_rank_fusion(rankings, k=self.rrf_k)

        # ④ 时间衰减重排（RRF 分作为分数基底，与相似度同量纲地缩减）
        now = datetime.now()
        results: list[SearchResult] = []
        for doc_id, rrf_score in fused:
            record = records.get(doc_id)
            if record is None:
                continue
            age_days = max(0.0, (now - record.created_at).total_seconds() / 86400.0)
            results.append(
                SearchResult(
                    record=record,
                    score=combined_score(
                        rrf_score,
                        age_days,
                        half_life_days=self.half_life_days,
                        decay_exponent=self.decay_exponent,
                    ),
                    raw_similarity=similarity_by_id.get(doc_id, 0.0),
                )
            )
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: self.memory_top_k]

    @staticmethod
    def _boost_facts_by_emotion(facts: list[Fact], emotion: str) -> list[Fact]:
        """冷层：同情绪事实优先（保持 importance 为次级排序键）。"""
        return sorted(
            facts,
            key=lambda f: (f.emotion_tag == emotion, f.importance, f.created_at),
            reverse=True,
        )

    # ---------- 写入（回复后事件驱动，参照③④）----------

    def remember_turn(
        self,
        companion_id: str,
        user_text: str,
        assistant_text: str,
        *,
        turn_index: int = 0,
        source: str = "",
        subject: str = "用户",
        emotion: str | None = None,
    ) -> dict[str, int]:
        """回复完成后的一次写入：抽取事实 → 向量入库 → 摘要增量并入。

        参数:
            emotion: 本轮情绪标签（英文）；写入事实的 emotion_tag 与向量的
                metadata，供后续按情绪加权召回（参照⑤）。

        返回写入统计（facts / memory / summary），异常降级不阻断。
        """
        stats = {"facts": 0, "memory": 0, "summary": 0}

        # ① 事件驱动抽取（结构化事实 + 摘要行）
        try:
            result = self.extractor.extract(
                user_text,
                assistant_text,
                companion_id=companion_id,
                source=source,
                subject=subject,
            )
        except Exception:  # 抽取失败不影响对话
            result = None

        # ② 冷层：事实入库（带上本轮情绪标签）
        if result is not None:
            for fact in result.facts:
                try:
                    if emotion:
                        fact.emotion_tag = emotion
                    self.cold.save_fact(companion_id, fact)
                    stats["facts"] += 1
                except Exception:
                    break

        # ③ 温层：本轮用户话语向量化入库（供后续混合召回 + 情绪加权）
        try:
            memory_id = self.warm.add(
                companion_id,
                user_text,
                metadata={"turn": turn_index, "source": source, "emotion": emotion},
            )
            self._sync_lexical(companion_id, memory_id, user_text)
            stats["memory"] = 1
        except Exception:
            pass

        # ④ 冷层：摘要滚动增量并入（设计参照④）
        if result is not None and result.summary_line:
            try:
                self.cold.append_summary(companion_id, turn_index, result.summary_line)
                stats["summary"] = 1
            except Exception:
                pass

        return stats
