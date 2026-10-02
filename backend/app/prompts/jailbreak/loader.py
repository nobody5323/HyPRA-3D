"""叙事框架层（jailbreak）预设加载器：YAML 与代码分离（同 persona / style 约定）。

只读资源：预设随程序分发，落在 `app/prompts/jailbreak/presets/`。
用户自建框架预设（工坊）暂未接入 `app/studio/`——那一层要处理删除清单、
id 与文件名一致性等约定，属于独立改动；本模块先提供内置预设与单文件加载，
后续接工坊时复用 `load_jailbreak_file` 即可。
"""

from pathlib import Path

import yaml

from app.paths import resource_path
from app.prompts.jailbreak.models import JailbreakPreset
from app.prompts.persona.loader import PersonaPreset

#: 启用本层但没指定预设时用的档位。选它而不是「按文件名排序取第一个」：
#: 默认档必须是**最保守**的那一个，排序结果会随新增文件漂移。
DEFAULT_PRESET_ID = "immersive-narrative"


def presets_dir() -> Path:
    """内置框架预设目录（随程序分发的只读资源）。"""
    return resource_path("app", "prompts", "jailbreak", "presets")


def load_jailbreak_file(file_path: str | Path) -> JailbreakPreset:
    """加载并校验单个框架预设 YAML。"""
    path = Path(file_path)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"框架预设 {path.name} 顶层必须是映射（dict）")
    return JailbreakPreset.model_validate(raw)


def load_builtin_jailbreaks() -> dict[str, JailbreakPreset]:
    """加载 presets/ 目录下全部 *.yaml，按 id 建索引。

    id 重复直接抛错（与 `load_builtin_styles` 一致）：内置资源重复 id 是打包/编写
    错误，早失败比运行期静默取其中一个好定位。
    """
    presets: dict[str, JailbreakPreset] = {}
    for file_path in sorted(presets_dir().glob("*.yaml")):
        preset = load_jailbreak_file(file_path)
        if preset.id in presets:
            raise ValueError(f"框架预设 id 重复：{preset.id}")
        presets[preset.id] = preset
    return presets


def check_persona_compatibility(
    persona: PersonaPreset, preset: JailbreakPreset
) -> list[str]:
    """校验「人设 × 框架」是否冲突，返回告警列表（不阻断）。

    规则与 `style.check_persona_compatibility` 一致：框架声明的 `conflicts_with`
    关键词若出现在人设的标签 / 描述 / 正文里，说明二者气质相冲
    （如「严格入戏」配一份本就经常打破第四面墙的设定），提示使用者确认。
    """
    warnings: list[str] = []
    haystack = " ".join([*persona.tags, persona.description, persona.prompt])
    for word in preset.conflicts_with:
        if word and word in haystack:
            warnings.append(
                f"叙事框架「{preset.name}」与人设「{persona.name}」可能冲突"
                f"（命中特质：{word}），请确认表达效果"
            )
    return warnings
