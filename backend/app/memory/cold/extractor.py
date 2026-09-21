"""回复后事件驱动抽取（设计参照③）。

时机：**一轮回复完成后**触发（非定时全量重扫，参照③）。
产出：结构化事实（写入冷层）+ 对已有事实的状态变更指令。

本模块提供：
- TurnExtractor        抽取器抽象
- RuleBasedExtractor   规则版（**无 LLM 依赖**，用于开发/测试/离线演示与降级）
- LLMExtractor       LLM 版（按内容判断 importance，并判定语义冲突与事件闭环）

两类抽取器的差异：
- 规则版只认固定正则，importance 按**类型常量**给值，且无法判断语义冲突；
- LLM 版按内容评分，并输出 updates（stale / resolved / touch）
  接通事实的**生命周期状态机**。

降级：LLM 版失败（网络/格式/超时）时**自动回退规则版**，不阻断对话。
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from app.llm.base import ChatMessage, LLMProvider
from app.memory.cold.models import Fact, FactType

logger = logging.getLogger(__name__)

# 时间词（作为 occurred_at 的粗略锚点）
_TIME_WORDS = ["今天", "昨晚", "昨天", "前天", "上周", "上个月", "去年", "最近", "明天", "下周"]


@dataclass
class FactUpdate:
    """对**已有**事实的状态变更指令（由抽取器判定）。

    三态语义见 `FactStatus`：
    - stale：被新信息**取代**（换了工作、搬家、分手）；
    - resolved：已有事实描述的事件**已闭环**（「打算谈加薪」→「谈成了」）；
    - touch：本轮**再次提到**（刷新 last_seen_at 并累积 confidence）。
    """

    fact_id: str
    action: Literal["stale", "resolved", "touch"]
    reason: str = ""


@dataclass
class ExtractionResult:
    """一轮对话的抽取结果。"""

    facts: list[Fact] = field(default_factory=list)
    updates: list[FactUpdate] = field(default_factory=list)
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
        known_facts: list[Fact] | None = None,
    ) -> ExtractionResult:
        """从一轮对话中抽取结构化事实与已有事实的状态变更。

        参数:
            known_facts: 与本轮相关的现有事实（由上层用混合检索召回）。
                LLM 版据此判断语义冲突与事件闭环；规则版据此做三元组去重。
        """


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
        known_facts: list[Fact] | None = None,
    ) -> ExtractionResult:
        facts: list[Fact] = []
        updates: list[FactUpdate] = []
        seen: set[tuple[str, str, str]] = set()   # 去重（同轮重复命中）
        occurred_at = self._extract_time(user_text)

        # 已有事实的三元组索引：完全相同时不重复入库，改为 touch。
        # 这一步交给规则（零成本、零误判），语义冲突才交给 LLM。
        known_index = {
            (fact.type.value, fact.subject, fact.predicate, fact.object): fact.fact_id
            for fact in (known_facts or [])
        }

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

                existing_id = known_index.get(
                    (fact_type.value, subject, predicate, obj)
                )
                if existing_id:
                    updates.append(
                        FactUpdate(
                            fact_id=existing_id,
                            action="touch",
                            reason="规则：与已有事实三元组完全一致",
                        )
                    )
                    continue

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
            updates=updates,
            keywords=[f.object for f in facts],
        )


_EXTRACT_SYSTEM_PROMPT = """你是长期陪伴型 AI 角色的记忆抽取器。

任务：从用户这一轮说的话里抽取值得长期记住的**结构化事实**，并判断已有事实是否需要更新状态。

只输出一个 JSON 对象，不要任何解释文字。格式：
{"facts": [...], "updates": [...]}

facts 每条：
{"type": "identity|preference|relationship|event|emotion_pattern|ongoing|other",
 "subject": "<主体，一般是用户>", "predicate": "<关系或属性>", "object": "<客体>",
 "importance": <1-5>, "detail": "<可选补充>"}

updates 每条（仅针对下列已给出的事实）：
{"fact_id": "<已有事实的 id>", "action": "stale|resolved|touch", "reason": "<简短原因>"}

importance 评分标准：
5 = 身份根基（姓名、职业、家庭结构、重大健康史）
4 = 稳定关系或长期模式（母亲患高血压、压力大时容易失眠）
3 = 一般偏好与进行中事项（喜欢下雨天、在准备考试）
2 = 一次性事件（今天去了咖啡馆）
1 = 无关紧要的细节

updates 的判断：
- stale：新信息**取代**了已有事实（换了工作、搬家、分手）
- resolved：已有事实描述的事件**已有结局**（「打算谈加薪」→「谈成了」）
- touch：本轮**再次提到**已有事实（表示仍然成立，用于累积可信度）

要求：
- 只抽用户明确说出的内容，不要推测；
- 没有可抽的内容时返回 {"facts": [], "updates": []}；
- fact_id 必须来自下面给出的已有事实列表，不要编造。
"""


def _parse_json_object(text: str) -> dict | None:
    """从模型输出中提取 JSON 对象（容忍 ```json 代码块包裹与前后缀文字）。"""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[A-Za-z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class LLMExtractor(TurnExtractor):
    """LLM 版抽取器（按内容判断重要性 + 判定事实生命周期）。

    相比规则版补齐三件事：
    1. **覆盖率**：不依赖固定正则，能抽到规则盖不住的表述；
    2. **importance 按内容评分**（规则版是按类型给常量，导致梭点机制失效）；
    3. **状态变更指令**：判定语义冲突（换工作 → stale）与事件闭环
       （谈成了 → resolved）——这是事实状态机唯一的触发来源。

    失败降级：网络/超时/格式错误时回退 `fallback`（默认规则版），
    不抛异常、不阻断对话。
    """

    name = "llm"

    def __init__(
        self,
        llm_provider: LLMProvider,
        *,
        fallback: TurnExtractor | None = None,
        max_tokens: int = 700,
        temperature: float = 0.0,
    ) -> None:
        self.llm = llm_provider
        self.fallback = fallback or RuleBasedExtractor()
        self.max_tokens = max_tokens
        # 抽取要求稳定输出（不需要创造性），温度取 0
        self.temperature = temperature

    def extract(
        self,
        user_text: str,
        assistant_text: str,
        *,
        companion_id: str,
        source: str = "",
        subject: str = "用户",
        known_facts: list[Fact] | None = None,
    ) -> ExtractionResult:
        known = list(known_facts or [])
        try:
            return self._extract_via_llm(
                user_text, assistant_text, source=source, subject=subject, known=known
            )
        except Exception as exc:  # noqa: BLE001 - 抽取失败不应影响对话
            logger.warning("LLM 抽取失败，降级为规则抽取：%s", exc)
            return self.fallback.extract(
                user_text,
                assistant_text,
                companion_id=companion_id,
                source=source,
                subject=subject,
                known_facts=known,
            )

    def _build_user_prompt(
        self, user_text: str, assistant_text: str, known: list[Fact]
    ) -> str:
        lines = ["【本轮对话】", f"用户：{user_text}", f"助手：{assistant_text}", ""]
        lines.append("【已有事实（供判断取代与闭环；fact_id 只能取自这里）】")
        if known:
            lines.extend(
                f"- id={fact.fact_id} | {fact.type.value} | "
                f"{fact.subject} {fact.predicate} {fact.object} | "
                f"importance={fact.importance}"
                for fact in known
            )
        else:
            lines.append("（无）")
        return "\n".join(lines)

    def _extract_via_llm(
        self,
        user_text: str,
        assistant_text: str,
        *,
        source: str,
        subject: str,
        known: list[Fact],
    ) -> ExtractionResult:
        raw = self.llm.chat(
            [
                ChatMessage(role="system", content=_EXTRACT_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=self._build_user_prompt(user_text, assistant_text, known),
                ),
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        payload = _parse_json_object(raw)
        if payload is None:
            raise ValueError("模型输出不是合法 JSON")

        facts = [
            fact
            for fact in (
                self._to_fact(item, subject=subject, source=source)
                for item in _as_list(payload.get("facts"))
            )
            if fact is not None
        ]
        known_ids = {fact.fact_id for fact in known}
        updates = [
            update
            for update in (
                self._to_update(item, known_ids)
                for item in _as_list(payload.get("updates"))
            )
            if update is not None
        ]
        return ExtractionResult(
            facts=facts, updates=updates, keywords=[f.object for f in facts]
        )

    @staticmethod
    def _to_fact(item: object, *, subject: str, source: str) -> Fact | None:
        """把一条 LLM 输出转成 Fact；单条格式不合法时跳过它而非丢弃整批。"""
        if not isinstance(item, dict):
            return None
        obj = str(item.get("object") or "").strip()
        if not obj:
            return None
        try:
            importance = int(item.get("importance", 3))
        except (TypeError, ValueError):
            importance = 3
        try:
            return Fact(
                type=FactType(str(item.get("type") or "other").strip().lower()),
                subject=str(item.get("subject") or subject).strip() or subject,
                predicate=str(item.get("predicate") or "提到").strip() or "提到",
                object=obj,
                detail=str(item.get("detail") or "").strip(),
                importance=max(1, min(5, importance)),
                confidence=0.8,
                source=source,
                keywords=[obj],
            )
        except Exception:  # noqa: BLE001 - 枚举值非法等
            return None

    @staticmethod
    def _to_update(item: object, known_ids: set[str]) -> FactUpdate | None:
        """把一条 LLM 输出转成 FactUpdate；fact_id 必须来自已有事实（防幻觉）。"""
        if not isinstance(item, dict):
            return None
        fact_id = str(item.get("fact_id") or "").strip()
        action = str(item.get("action") or "").strip().lower()
        if fact_id not in known_ids or action not in {"stale", "resolved", "touch"}:
            return None
        return FactUpdate(
            fact_id=fact_id,
            action=action,  # type: ignore[arg-type]
            reason=str(item.get("reason") or "").strip(),
        )


def _as_list(value: object) -> list:
    """把 LLM 输出中的期望列表字段归一化（模型偶尔会返回 None 或非列表）。"""
    return value if isinstance(value, list) else []


def create_extractor(
    name: str = "rule", *, llm_provider: LLMProvider | None = None
) -> TurnExtractor:
    """按名称创建抽取器。

    参数:
        name: rule（规则版，零依赖）| llm（LLM 版，覆盖率与准确性更高）；
        llm_provider: LLM 版必需；未提供时**退回规则版**（不阻断启动）。
    """
    key = (name or "rule").strip().lower()
    if key in {"rule", "rule-based", "rules"}:
        return RuleBasedExtractor()
    if key in {"llm", "model"}:
        if llm_provider is None:
            logger.warning("未提供 LLM provider，抽取器退回规则版")
            return RuleBasedExtractor()
        return LLMExtractor(llm_provider)
    raise ValueError(f"未知抽取器：{name!r}（可选 rule | llm）")
