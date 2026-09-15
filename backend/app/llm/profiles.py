"""模型预设档：per-model 的采样参数、推理开关与技术性输出约束。

背景：不同模型对采样参数的容忍度、对格式指令的敏感度、对推理链的默认行为不同
（例：推理模型默认先生成上千思考 token，情感陪伴场景实测慢约 6 倍；某些模型爱输出
markdown 列表与 emoji，需要额外提示压制）。本档记录「技术适配」，与「文风预设」
正交互补，两者合并后交给 provider。

选择规则（resolve_profile，优先级从高到低）：
    ① 界面显式选定的 preset_id（POST /chat 的 preset_id）
    ② 按 match 子串匹配模型名（多个命中取最长匹配）
    ③ 都不匹配 → default（match: ["*"]）

合并规则（resolve_sampling）：
    文风预设的 sampling 覆盖本档 sampling（文风更贴近内容意图）；
    本档独有的键（top_p / style_hint / enable_thinking）保留。
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_PROFILES_FILE = Path(__file__).resolve().parent / "profiles.yaml"


class ModelProfile(BaseModel):
    """一个模型的预设档。"""

    id: str = Field(description="档位标识（同时作为界面可选值 preset_id）")
    label: str = Field(default="", description="界面展示名（中文，缺省用 id）")
    description: str = Field(default="", description="界面展示的一句话说明")
    match: list[str] = Field(default_factory=list, description="模型名匹配模式（子串）")
    temperature: float = Field(default=0.8, description="采样温度")
    top_p: float | None = Field(default=None, description="核采样阈值")
    frequency_penalty: float | None = Field(default=None, description="频率惩罚（抑制复读）")
    presence_penalty: float | None = Field(default=None, description="存在惩罚")
    max_tokens: int | None = Field(default=None, description="回复长度上限")
    enable_thinking: bool | None = Field(
        default=None,
        description="推理模型思考开关：False 关闭（提速）、True 开启、None 不传该参数",
    )
    style_hint: str = Field(default="", description="该模型特有的表达约束（附加到风格块）")

    @property
    def display_name(self) -> str:
        """界面展示名（缺省回落到 id）。"""
        return self.label or self.id

    @property
    def sampling(self) -> dict[str, float | int | None]:
        """本档的采样参数（未设置的项为 None，合并时跳过）。"""
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "max_tokens": self.max_tokens,
        }


@dataclass
class ResolvedSampling:
    """模型预设 ⊕ 文风预设 合并后的最终采样参数。"""

    temperature: float = 0.8
    max_tokens: int | None = None
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    style_hint: str = ""
    profile_id: str = ""
    profile_label: str = ""
    enable_thinking: bool | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def to_provider_kwargs(self) -> dict:
        """转成 provider.chat 的关键字参数（跳过 None，不污染请求）。"""
        kwargs: dict = {"temperature": self.temperature}
        for key in ("max_tokens", "top_p", "frequency_penalty", "presence_penalty"):
            value = getattr(self, key)
            if value is not None:
                kwargs[key] = value
        return kwargs

    def to_public_dict(self) -> dict:
        """给前端的预设摘要（不含 style_hint 全文，避免界面噪声）。"""
        return {
            "preset_id": self.profile_id,
            "preset_label": self.profile_label,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "max_tokens": self.max_tokens,
            "enable_thinking": self.enable_thinking,
            "sources": dict(self.sources),
        }


def load_model_profiles(file_path: str | Path = _PROFILES_FILE) -> list[ModelProfile]:
    """加载全部模型预设档（保序）。"""
    path = Path(file_path)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    items = raw.get("profiles") or []
    if not isinstance(items, list) or not items:
        raise ValueError(f"模型预设档 {path.name} 缺少 profiles 列表")
    return [ModelProfile.model_validate(item) for item in items]


def resolve_profile(
    model_name: str,
    profiles: list[ModelProfile] | None = None,
    preset_id: str | None = None,
) -> ModelProfile:
    """解析本轮生效的预设档。

    参数:
        model_name: 当前模型名（用于 match 匹配）；
        profiles: 预设档列表（缺省从 profiles.yaml 读取）；
        preset_id: 界面显式选定的档位 id —— **优先级最高**，未知 id 时回落到匹配规则。
    """
    items = profiles if profiles is not None else load_model_profiles()

    # ① 显式选定优先
    if preset_id:
        selected = next((p for p in items if p.id == preset_id), None)
        if selected is not None:
            return selected

    # ② 按模型名匹配（取最长子串）
    best: ModelProfile | None = None
    best_len = -1
    for profile in items:
        for pattern in profile.match:
            if pattern == "*":
                continue
            if pattern and pattern.lower() in (model_name or "").lower():
                if len(pattern) > best_len:
                    best, best_len = profile, len(pattern)
    if best is not None:
        return best

    # ③ 通配档兜底
    fallback = next((p for p in items if "*" in p.match), None)
    return fallback or items[-1]


def resolve_sampling(
    model_name: str,
    style_sampling: dict[str, float] | None = None,
    profiles: list[ModelProfile] | None = None,
    preset_id: str | None = None,
) -> ResolvedSampling:
    """合并「模型预设档」与「文风预设建议参数」为最终采样参数。

    参数:
        model_name: 当前使用的模型名（用于匹配预设档）；
        style_sampling: 文风预设给出的建议参数（优先级更高）；
        profiles: 预设档列表（缺省从 profiles.yaml 读取）；
        preset_id: 界面显式选定的档位 id（优先级最高）。
    """
    profile = resolve_profile(model_name, profiles, preset_id)
    merged: dict = {}
    sources: dict[str, str] = {}

    # ① 模型预设为基础
    for key, value in profile.sampling.items():
        if value is not None:
            merged[key] = value
            sources[key] = profile.id

    # ② 文风预设覆盖（文风更贴近内容意图）
    for key, value in (style_sampling or {}).items():
        if value is not None:
            merged[key] = value
            sources[key] = "style"

    return ResolvedSampling(
        temperature=float(merged.get("temperature", 0.8)),
        max_tokens=int(merged["max_tokens"]) if merged.get("max_tokens") else None,
        top_p=merged.get("top_p"),
        frequency_penalty=merged.get("frequency_penalty"),
        presence_penalty=merged.get("presence_penalty"),
        style_hint=profile.style_hint.strip(),
        profile_id=profile.id,
        profile_label=profile.display_name,
        enable_thinking=profile.enable_thinking,
        sources=sources,
    )


def list_presets(profiles: list[ModelProfile] | None = None) -> list[dict]:
    """预设档清单（供 GET /chat/presets 返回，界面用于渲染选择器）。"""
    items = profiles if profiles is not None else load_model_profiles()
    return [
        {
            "id": p.id,
            "label": p.display_name,
            "description": p.description,
            "match": list(p.match),
            "temperature": p.temperature,
            "top_p": p.top_p,
            "frequency_penalty": p.frequency_penalty,
            "presence_penalty": p.presence_penalty,
            "max_tokens": p.max_tokens,
            "enable_thinking": p.enable_thinking,
        }
        for p in items
    ]
