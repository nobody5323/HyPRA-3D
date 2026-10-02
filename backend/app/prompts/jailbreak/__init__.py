"""jailbreak 预设：叙事框架层（可开关，默认关闭）。

术语沿用 SillyTavern 生态：ST 把这一层叫 `jailbreak`（Post-History Instructions），
本项目在 ST 预设组装路径里也用同一个词（见 `app/graph/nodes.py` 的
「文风层追加到 jailbreak 槽位之后」）。这里把它补进**内置分层路径**，
让不走 ST 预设的用户也能用上同一层能力。

与本包其它预设的分工：

    persona/      谁在说话
    style/        怎么说话
    jailbreak/    这次对话处在什么**框架**里（虚构叙事 / 现实问答）

三层正交，可任意组合。框架层只回答「以什么身份、按什么规则回应」，
不描述性格（那是 persona）、不描述句长语气（那是 style）。
"""

from app.prompts.jailbreak.loader import (
    DEFAULT_PRESET_ID,
    load_builtin_jailbreaks,
    load_jailbreak_file,
    presets_dir,
)
from app.prompts.jailbreak.models import JailbreakPreset

__all__ = [
    "DEFAULT_PRESET_ID",
    "JailbreakPreset",
    "load_builtin_jailbreaks",
    "load_jailbreak_file",
    "presets_dir",
]
