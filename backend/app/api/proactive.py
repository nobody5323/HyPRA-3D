"""主动链路接口：状态 / 手动触发 / 重置节流。

设计见 `docs/proactive-multimodal.md` §5。三个接口各有明确用途：

| 接口 | 用途 |
| --- | --- |
| `GET /proactive/status` | 界面显示「今天已经主动开口几次」「下次为什么不说」 |
| `POST /proactive/run` | **演示与调试**：立刻评估一轮，不等下一个 60 秒周期 |
| `POST /proactive/reset` | 重置节流（冷却 / 当日计数 / 去重表） |

`POST /proactive/run` 是刻意开放的：调主动沟通最难的就是「等它自己到点」——
演示视频里不可能等 90 分钟冷却。它走的是**同一条** `run_once`，
因此不会绕过任何一道闸门（要强制开口请改配置，而不是加一条绕过闸门的后门）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import get_settings
from app.proactive.runner import get_proactive_runner
from app.proactive.state import get_state_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proactive", tags=["proactive"])


class ProactiveRunResponse(BaseModel):
    """一次手动评估的结果。"""

    fired: bool = Field(description="是否真的开口了")
    trigger_id: str = ""
    reason: str = ""
    skipped_reason: str = Field(
        default="", description="被闸门拦下的原因（为空表示没有意图，或已成功开口）"
    )
    declined: bool = Field(default=False, description="角色选择不说")
    reply: str = ""


@router.get("/status")
def proactive_status() -> dict:
    """主动链路的配置、节流状态与触发器清单。"""
    return get_proactive_runner().status()


@router.post("/run", response_model=ProactiveRunResponse)
async def run_proactive_now() -> ProactiveRunResponse:
    """立刻评估一轮（演示 / 调试）。

    ⚠️ 有订阅者才会真的生成：SSE 通道没人监听时，事件会被总线丢弃，
    生成纯属浪费一次 LLM 调用（见 `runner.run_once` 的说明）。
    因此用这个接口做演示时，**必须先打开对话界面**。
    """
    outcome = await get_proactive_runner().run_once()
    return ProactiveRunResponse(
        fired=outcome.fired,
        trigger_id=outcome.trigger_id,
        reason=outcome.reason,
        skipped_reason=outcome.skipped_reason,
        declined=outcome.declined,
        reply=outcome.reply,
    )


class ProactiveResetResponse(BaseModel):
    """重置结果。"""

    reset: bool = True


@router.post("/reset", response_model=ProactiveResetResponse)
def reset_proactive_state() -> ProactiveResetResponse:
    """清空节流状态（冷却锚点 / 当日计数 / 触发器去重表）。

    用户改完「免打扰时段 / 每日上限」后想立刻验证时用得上——
    否则要等到冷却自然过期。
    """
    settings = get_settings()
    get_state_store(settings.proactive_state_path).reset()
    logger.info("主动链路节流状态已重置")
    return ProactiveResetResponse(reset=True)
