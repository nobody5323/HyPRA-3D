"""AI 预设适配：导入酒馆预设后，一键改造成 HyPRA 的陪伴对话形态。

分两层（各自模块说明里有更详细的边界说明）：

- **规则层** `detector`：确定性体检 + 确定性修复，不调用模型。
  产出问题清单、可直接应用的覆盖层补丁、以及需要模型改写的条目清单。
- **规划层** `planner`：把需要语义改写的条目攒成**一次**模型调用，
  校验模型返回后合成最终覆盖层补丁。

合规（AGENTS.md §6）：本包只提供**适配机制**，不含任何 SillyTavern 或社区预设的
提示词正文；预设正文始终是用户本地数据，不进仓库、不随发行物分发。
"""

from app.prompts.adaptation.detector import detect
from app.prompts.adaptation.diff import build_diff
from app.prompts.adaptation.loader import load_rules, load_rules_file
from app.prompts.adaptation.models import (
    AdaptationRules,
    ContentRule,
    DetectionResult,
    Finding,
    PlanResult,
    PresetRule,
    RewriteOutcome,
    RewriteTask,
    TargetProfile,
)
from app.prompts.adaptation.planner import plan

__all__ = [
    "AdaptationRules",
    "ContentRule",
    "DetectionResult",
    "Finding",
    "PlanResult",
    "PresetRule",
    "RewriteOutcome",
    "RewriteTask",
    "TargetProfile",
    "build_diff",
    "detect",
    "load_rules",
    "load_rules_file",
    "plan",
]
