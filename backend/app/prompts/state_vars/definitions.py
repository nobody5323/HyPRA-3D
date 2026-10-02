"""状态变量定义：注册表 + 默认值。

「动态状态变量」（如 {{current_mood}}）借鉴 SillyTavern 的宏替换思想，但为自写实现。
每个变量集中在此声明 默认值 / 说明 / 示例，便于替换器统一兜底。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StateVar:
    """单个状态变量的元信息。"""

    name: str          # 变量名（不含 {{ }}）
    description: str   # 含义说明
    default: str       # 缺失时的兜底默认值（不静默失败）
    example: str       # 用法示例


# 内置状态变量注册表：后续新增变量在此追加即可（如 {{current_time}}）
BUILTIN_STATE_VARS: dict[str, StateVar] = {
    var.name: var
    for var in [
        StateVar(
            name="user_name",
            description="当前对话用户（来访者）的称呼",
            default="朋友",
            example="小林",
        ),
        StateVar(
            name="char_name",
            description="AI 角色（本预设）的名字（对话链路会传入真实角色名，此处只是兜底）",
            default="角色",
            example="阿岸",
        ),
        StateVar(
            name="current_mood",
            description="用户当前情绪标签（M4 情绪链路接入前由对话前端传入）",
            default="平静",
            example="低落",
        ),
    ]
}


def get_state_var(name: str) -> StateVar | None:
    """按名称取变量定义，不存在返回 None。"""
    return BUILTIN_STATE_VARS.get(name)
