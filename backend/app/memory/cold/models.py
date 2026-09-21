"""冷层结构化事实数据模型。

字段设计围绕「回复后事件驱动抽取」（设计参照③）与「分层职责」（参照⑦）：
长期核心事实常驻、importance 锚点防人设崩塌、status 支持事件闭环。
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class FactType(str, Enum):
    """事实类型（LLM 抽取时的分类枚举）。"""

    IDENTITY = "identity"                # 用户身份/画像
    PREFERENCE = "preference"            # 偏好喜好
    RELATIONSHIP = "relationship"        # 人物关系
    EVENT = "event"                      # 关键事件
    EMOTION_PATTERN = "emotion_pattern"  # 情绪模式与压力源
    ONGOING = "ongoing"                  # 进行中事项
    OTHER = "other"                      # 其他


class FactStatus(str, Enum):
    """事实生命周期状态。"""

    ACTIVE = "active"      # 活跃（参与召回）
    STALE = "stale"        # 过时（降权，可被清理）
    RESOLVED = "resolved"  # 已解决/事件闭环（保留记录但不再召回）


class Fact(BaseModel):
    """一条结构化事实（三元组 + 元数据）。"""

    fact_id: str = Field(default_factory=lambda: "", description="主键（入库时自动生成 UUID）")
    type: FactType = Field(description="事实类型")
    subject: str = Field(description="主体，如「小林」")
    predicate: str = Field(description="关系/属性动词，如「喜欢」「在…工作」")
    object: str = Field(description="客体/宾语，如「下雨天」")
    detail: str = Field(default="", description="自然语言细节补充")
    occurred_at: str | None = Field(default=None, description="事件发生时间（相对/绝对文本）")
    created_at: datetime = Field(default_factory=datetime.now, description="写入时间（衰减基准）")
    last_seen_at: datetime | None = Field(default=None, description="最近被对话印证的时间")
    importance: int = Field(default=3, ge=1, le=5, description="重要性 1-5，>=4 视为长期锚点")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="置信度 0-1")
    status: FactStatus = Field(default=FactStatus.ACTIVE, description="生命周期状态")
    emotion_tag: str | None = Field(default=None, description="关联情绪标签（M4 加权预留）")
    keywords: list[str] = Field(default_factory=list, description="召回关键词（未来向量化）")
    source: str = Field(default="", description="来源（session_id / 摘要 / 主动告知）")

    @field_validator("keywords")
    @classmethod
    def _strip_keywords(cls, v: list[str]) -> list[str]:
        """清洗关键词：去空白、去空串。"""
        return [k.strip() for k in v if k and k.strip()]

    @property
    def anchor(self) -> bool:
        """是否为长期核心锚点（importance >= 4）。"""
        return self.importance >= 4

    @property
    def summary_text(self) -> str:
        """三元组的人类可读摘要（检索拼接用）。"""
        parts = [self.subject, self.predicate, self.object]
        if self.occurred_at:
            parts.append(f"（{self.occurred_at}）")
        return " ".join(p for p in parts if p)
