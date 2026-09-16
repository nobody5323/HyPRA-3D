"""HyPRA 演示用 MCP 服务器（原创内容，独立进程）。

用途：演示「数字人通过 MCP 调用外部能力办事」——
本文件作为**独立的 MCP 服务器进程**运行，HyPRA 后端作为 MCP **Client** 连接它，
把这里定义的工具接入 Agent 行动层，模型即可在对话中调用。

合规声明（AGENTS.md §6）：本文件内容 100% 原创，不含任何第三方提示词、语料或素材；
音频建议只给出**文字描述**（不内置任何受版权保护的音频素材）。

手动启动（便于用 MCP Inspector 等工具调试）：
    cd backend && python mcp_servers/wellness_server.py
正常情况下由 HyPRA 后端按 mcp_servers.json 自动以 stdio 方式拉起。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hypra-wellness")

# ---------------------------------------------------------------
# 情绪 → 环境音建议（文字描述，原创）
# ---------------------------------------------------------------
_SOUND_SUGGESTIONS: dict[str, tuple[str, str]] = {
    "anxious": ("雨声 + 低频钢琴", "把注意力放到持续的雨声上，让呼吸跟着雨点的节奏慢下来"),
    "sad": ("壁炉噼啪声 + 大提琴长音", "允许自己待一会儿，不用急着好起来"),
    "tired": ("白噪音 + 缓慢海浪", "音量调到刚好盖过环境噪声即可，别再消耗注意力"),
    "angry": ("溪流声 + 空灵人声吟唱", "先让身体松下来，情绪会跟着退潮"),
    "happy": ("轻快民谣吉他 + 鸟鸣", "把这份轻快记下来，之后也能回来听"),
    "calm": ("森林环境音 + 偶尔风铃", "保持就好，什么都不用做"),
    "surprised": ("钢琴单音循环", "给自己几秒钟消化刚才的信息"),
    "neutral": ("咖啡馆背景音 + 翻书声", "有一点人间烟火气，又不打扰"),
}


@mcp.tool()
def recommend_ambient_sound(emotion: str, minutes: int = 15) -> str:
    """按用户当前情绪推荐一段环境音（用于放松、专注或入睡）。

    Args:
        emotion: 情绪标签，如 anxious / sad / tired / happy / calm / neutral
        minutes: 建议聆听时长（分钟）
    """
    key = (emotion or "neutral").strip().lower()
    sound, tip = _SOUND_SUGGESTIONS.get(key, _SOUND_SUGGESTIONS["neutral"])
    return f"推荐环境音：{sound}；建议聆听 {minutes} 分钟。{tip}。"


@mcp.tool()
def suggest_breathing_pattern(stress_level: int) -> str:
    """按压力强度给出呼吸节奏建议。

    Args:
        stress_level: 压力强度 0-10（0 很放松，10 非常紧绷）
    """
    level = max(0, min(10, int(stress_level)))
    if level >= 8:
        pattern = "4-7-8 呼吸：吸气 4 秒 → 屏息 7 秒 → 缓慢呼气 8 秒，重复 4 轮"
        note = "压力偏高时先别急着解决问题，把注意力交给呼吸"
    elif level >= 5:
        pattern = "方箱呼吸：吸气 4 秒 → 屏息 4 秒 → 呼气 4 秒 → 屏息 4 秒，重复 6 轮"
        note = "这个节奏适合在会议前或入睡前使用"
    else:
        pattern = "自然腹式呼吸：吸气 4 秒 → 呼气 6 秒，重复 5 轮"
        note = "状态不错，用它把这份平稳延续下去"
    return f"（压力强度 {level}/10）{pattern}。{note}。"


if __name__ == "__main__":
    mcp.run()
