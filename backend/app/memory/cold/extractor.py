"""回复后事件驱动抽取（设计参照③）。

时机：**一轮回复完成后**触发（非定时全量重扫，参照③）。
产出：结构化事实（写入冷层）。

本模块提供：
- TurnExtractor        抽取器抽象
- RuleBasedExtractor   规则版（**无 LLM 依赖**，用于开发/测试/离线演示）
- 预留 LLM 抽取实现位置（接入模型后启用，接口不变）

注意：规则版是"可用但保守"的实现——只抽取高置信模式，
置信度记 0.6（低于 LLM 的 0.8），并由 status/confidence 支持后续修正。
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.memory.cold.models import Fact, FactType

# 时间词（作为 occurred_at 的粗略锚点）
_TIME_WORDS = ["今天", "昨晚", "昨天", "前天", "上周", "上个月", "去年", "最近", "明天", "下周"]


@dataclass
class ExtractionResult:
    """一轮对话的抽取结果。"""

    facts: list[Fact] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


class TurnExtractor(ABC):
    """一轮对话的抽取器抽象。"""

    name: str = "base"

    @abstractmethod
    def extract(
        self,
        user_text: str,
        assistant_text: str,
        *,
        companion_id: str,
        source: str = "",
        subject: str = "用户",
    ) -> ExtractionResult:
        """从一轮对话中抽取结构化事实与摘要行。"""


class RuleBasedExtractor(TurnExtractor):
    """规则版抽取器：正则匹配高置信模式，无 LLM 依赖。"""

    name = "rule-based"

    # (类型, 正则, 三元组构造说明) —— 统一用「主语 + 谓词 + 宾语」
    _PATTERNS: list[tuple[FactType, re.Pattern[str]]] = [
        # 偏好：喜欢 / 爱吃 / 讨厌 / 不喜欢
        (FactType.PREFERENCE, re.compile(r"(?:很|特别|超)?(喜欢|爱|讨厌|不喜欢|害怕|怕)([^，。！？；\s]{1,14})")),
        # 情绪模式与压力源：失眠 / 压力 / 焦虑
        (FactType.EMOTION_PATTERN, re.compile(r"(失眠|睡不着|焦虑|压力大|压力|emo|崩溃|难受|心慌)")),
        # 人物关系：妈妈 / 爸 / 哥 / 老板 / 同事 / 朋友 / 恋人
        # 注意：长词在前，保证「妈妈」不会被单字「妈」截断
        (FactType.RELATIONSHIP, re.compile(r"(妈妈|爸爸|母亲|父亲|奶奶|爷爷|哥哥|弟弟|姐姐|妹妹|老板|领导|同事|室友|朋友|男友|女友|男朋友|女朋友|老公|老婆|孩子|妈|爸|哥|姐|弟|妹)")),
        # 进行中事项：打算 / 准备 / 想要 / 计划
        (FactType.ONGOING, re.compile(r"(?:打算|准备|计划|想要|想)([^，。！？；\s]{1,14})")),
        # 关键事件：换了 / 去了 / 搬到 / 辞职 / 分手
        (FactType.EVENT, re.compile(r"(换了|搬到了|搬家|辞职|离职|分手|失业|生病|住院|考试|面试)")),
    ]

    # 各类型默认重要性（≥4 视为长期锚点，参照⑦）
    _IMPORTANCE = {
        FactType.IDENTITY: 5,
        FactType.RELATIONSHIP: 4,
        FactType.EMOTION_PATTERN: 4,
        FactType.PREFERENCE: 3,
        FactType.ONGOING: 3,
        FactType.EVENT: 3,
        FactType.OTHER: 2,
    }

    def _extract_time(self, text: str) -> str | None:
        """粗略提取时间锚点（首个出现的时间词）。"""
        for word in _TIME_WORDS:
            if word in text:
                return word
        return None

    def extract(
        self,
        user_text: str,
        assistant_text: str,
        *,
        companion_id: str,
        source: str = "",
        subject: str = "用户",
    ) -> ExtractionResult:
        facts: list[Fact] = []
        seen: set[tuple[str, str, str]] = set()   # 去重（同轮重复命中）
        occurred_at = self._extract_time(user_text)

        for fact_type, pattern in self._PATTERNS:
            for match in pattern.finditer(user_text):
                groups = [g for g in match.groups() if g]
                if not groups:
                    continue
                if len(groups) >= 2:
                    predicate, obj = groups[0], groups[1]
                else:
                    predicate, obj = {"relationship": "与…有关系"}.get(
                        fact_type.value, "提到"
                    ), groups[0]

                key = (fact_type.value, predicate, obj)
                if key in seen:
                    continue
                seen.add(key)

                facts.append(
                    Fact(
                        type=fact_type,
                        subject=subject,
                        predicate=predicate,
                        object=obj,
                        occurred_at=occurred_at,
                        importance=self._IMPORTANCE.get(fact_type, 2),
                        confidence=0.6,          # 规则版保守置信度（LLM 版 0.8）
                        source=source,
                        keywords=[obj] if obj else [],
                    )
                )

        return ExtractionResult(
            facts=facts,
            keywords=[f.object for f in facts],
        )


def create_extractor(name: str = "rule") -> TurnExtractor:
    """按名称创建抽取器（llm 版接入模型后在此注册）。"""
    key = (name or "rule").strip().lower()
    if key in {"rule", "rule-based", "rules"}:
        return RuleBasedExtractor()
    if key in {"llm", "model"}:
        raise NotImplementedError(
            "LLM 抽取器尚未接入（需真实模型 key）；请在配置中使用抽规则版。"
        )
    raise ValueError(f"未知抽取器：{name!r}（可选 rule | llm）")
