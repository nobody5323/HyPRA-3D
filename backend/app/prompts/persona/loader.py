"""人设预设加载器：YAML 预设与代码分离。

预设文件为纯 YAML（见 presets/ 下的示例），本模块负责：
- 定位预设目录并读取指定/全部预设；
- 用 pydantic 做 schema 校验（缺失必填字段即报错，早失败）。
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.paths import resource_path


def presets_dir() -> Path:
    """内置人设预设目录（随程序分发的只读资源）。"""
    return resource_path("app", "prompts", "persona", "presets")


class PersonaPreset(BaseModel):
    """一份人设预设（对应一个 YAML 文件）。"""

    id: str = Field(description="预设唯一标识（英文小写连字符）")
    name: str = Field(description="角色名")
    title: str = Field(description="一句话角色定位")
    description: str = Field(description="角色简介")
    tags: list[str] = Field(default_factory=list, description="标签")
    creator: str = Field(default="hypra-original", description="内容作者标记")
    prompt: str = Field(description="人设正文（支持 {{变量}} 宏）")
    background: str = Field(
        default="",
        description=(
            "背景故事（常驻注入，排在人设正文之后、世界书之前）。"
            "刻意与人设正文分开：人设管「是谁、怎么说话」，背景管「过往与世界观」，"
            "用户自建角色时两块内容的写法差别很大"
        ),
    )
    variables: list[str] = Field(default_factory=list, description="声明用到的状态变量名")

    @property
    def path(self) -> Path | None:
        """该预设对应的 YAML 文件路径（加载时记录）。"""
        return getattr(self, "_path", None)


def load_preset_file(file_path: str | Path) -> PersonaPreset:
    """加载并校验单个预设 YAML 文件。"""
    path = Path(file_path)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"预设文件 {path.name} 顶层必须是映射（dict）")
    preset = PersonaPreset.model_validate(raw)
    object.__setattr__(preset, "_path", path)
    return preset


def load_builtin_presets() -> dict[str, PersonaPreset]:
    """加载 presets/ 目录下全部 *.yaml，按 id 建索引。"""
    presets: dict[str, PersonaPreset] = {}
    for file_path in sorted(presets_dir().glob("*.yaml")):
        preset = load_preset_file(file_path)
        if preset.id in presets:
            raise ValueError(f"预设 id 重复：{preset.id}")
        presets[preset.id] = preset
    return presets
