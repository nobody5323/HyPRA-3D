"""感知层：多模态输入 → 中性结构化事实 → 提示词。

设计见 `docs/proactive-multimodal.md` §4。**两条硬约定**（改这个包之前先读）：

1. **感知源只产出中性事实，不产出提示词**。渲染集中在 `context.py` 一处
   ——理由同 `AGENTS.md §9.4` 的 `datasource` 分工：多源共享一套渲染，
   否则每个源各写一套中文句子，措辞迟早漂移，而且没法统一做 TTL 与预算。
2. **感知数据不进记忆库**（§4.6）。它是「此刻发生了什么」，不是「关于这个人知道什么」；
   写进记忆会变成「我记得你上周三凌晨在听什么歌」这种越界。

三类感知源（`PerceptionSource` 的实现名）：

| 源 | 采集在哪 | 传什么 |
| --- | --- | --- |
| `asr` | 前端录音（MediaRecorder）→ 后端 | 音频 blob → 转写文本 |
| `vision` | 前端选图 → 后端 | 图片 → 结构化描述（P4，本期未实现） |
| `desktop` | Electron 主进程 → IPC → 渲染层 → 后端 | 结构化字段（歌 / 窗口 / 空闲 / 电量 / 时段） |

`vision` 本期**不做**：摄像头与连续屏幕感知已明确否决（§4.4 决策），
只保留「用户主动分享图片」这一条路径，且排在 P4。
"""

from app.perception.models import DesktopContext, PerceptionEvent
from app.perception.snapshot import PerceptionSnapshot, get_perception_snapshot, set_perception_snapshot

__all__ = [
    "DesktopContext",
    "PerceptionEvent",
    "PerceptionSnapshot",
    "get_perception_snapshot",
    "set_perception_snapshot",
]
