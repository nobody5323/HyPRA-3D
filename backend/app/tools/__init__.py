"""tools 子包：function calling 工具。

- emotion/     情绪识别与结构化输出（回复 + 情绪标签）：主通道 llm_channel，
               兜底策略可替换（fallback_regex / fallback_neutral）
- builtin_tools.py  内置情感陪伴工具（情绪日记 / 趋势 / 呼吸 / 记忆检索）
- registry.py  ToolSpec 注册表
"""
