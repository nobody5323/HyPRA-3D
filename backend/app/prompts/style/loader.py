"""风格预设加载器：YAML 与代码分离（同 persona 预设约定）。

并负责「风格 × 人设」的一致性校验（不阻断，仅返回告警）。
"""

from pathlib import Path

import yaml

from app.paths import resource_path
from app.prompts.persona.loader import PersonaPreset
from app.prompts.style.models import StylePreset


def presets_dir() -> Path:
    """内置风格预设目录（随程序分发的只读资源）。"""
    return resource_path("app", "prompts", "style", "presets")


def load_style_file(file_path: str | Path) -> StylePreset:
    """加载并校验单个风格预设 YAML。"""
    path = Path(file_path)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"风格预设 {path.name} 顶层必须是映射（dict）")
    return StylePreset.model_validate(raw)


def load_builtin_styles() -> dict[str, StylePreset]:
    """加载 presets/ 目录下全部 *.yaml，按 id 建索引。"""
    styles: dict[str, StylePreset] = {}
    for file_path in sorted(presets_dir().glob("*.yaml")):
        preset = load_style_file(file_path)
        if preset.id in styles:
            raise ValueError(f"风格预设 id 重复：{preset.id}")
        styles[preset.id] = preset
    return styles


def check_persona_compatibility(
    persona: PersonaPreset, style: StylePreset
) -> list[str]:
    """校验「人设 × 风格」是否冲突，返回告警列表（不阻断）。

    规则：风格声明的 conflicts_with 关键词若出现在人设的标签或描述/正文中，
    说明二者气质相冲（如「温柔倾听」配「毒舌」风格），提示使用者确认。
    """
    warnings: list[str] = []
    haystack = " ".join([*persona.tags, persona.description, persona.prompt])
    for word in style.conflicts_with:
        if word and word in haystack:
            warnings.append(
                f"风格「{style.name}」与人设「{persona.name}」可能冲突"
                f"（命中特质：{word}），请确认表达效果"
            )
    return warnings
