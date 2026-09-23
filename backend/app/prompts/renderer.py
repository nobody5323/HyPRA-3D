"""分层提示词组装器（M1 雏形）。

固定分层顺序（对齐设计参照 8 条之 1，后续各层在此扩展）：
    人设(prompt) → 状态变量块 → （世界书 → RAG → 滚动窗口…均为后续里程碑）

本阶段实现：
- 单层（人设正文，内含 {{变量}} 宏）解析并做 token 预算裁剪；
- token 估算用启发式近似（见 estimate_tokens 注释），预留 replace 接口，
  接入真实模型后替换为对应 tokenizer（Qwen 用 tiktoken 计数不准确，故不引入）。
"""

from dataclasses import dataclass, field

from app.prompts.persona.loader import PersonaPreset
from app.prompts.state_vars.resolver import resolve_template

# 中文启发式：1 个 CJK 字符 ≈ 0.7 token；其余字符按 4 字符 ≈ 1 token 粗估
_CJK_TOKEN_RATIO = 0.7
_ASCII_CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    """启发式估算 token 数（仅用于预算裁剪，非精确值）。

    中文约占 0.7 token/字符、英文等约 4 字符/token。TODO: 接入真实
    Qwen tokenizer 后以 tokenizers 库替换本实现，接口保持不变。
    """
    cjk_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other_chars = len(text) - cjk_chars
    return int(cjk_chars * _CJK_TOKEN_RATIO + other_chars / _ASCII_CHARS_PER_TOKEN)


def truncate_to_budget(text: str, max_tokens: int) -> tuple[str, bool]:
    """按 token 预算从尾部裁剪文本；返回 (裁剪后文本, 是否发生裁剪)。

    采用「删尾重算」的简单策略：逐段丢掉末尾字符直到估算值不超预算，
    保证人设开头（身份基调）优先保留。
    """
    if estimate_tokens(text) <= max_tokens:
        return text, False

    step = max(1, len(text) // 20)  # 每次试探性删除的字符数
    truncated = text
    while estimate_tokens(truncated) > max_tokens and truncated:
        truncated = truncated[:-step] if len(truncated) > step else ""
    # 预留裁剪标记位，避免恰好顶满预算
    return truncated, True


@dataclass
class RenderedPrompt:
    """一次渲染的完整结果。"""

    persona: PersonaPreset
    text: str                    # 最终 System Prompt 文本
    warnings: list[str] = field(default_factory=list)
    truncated: bool = False      # 是否发生 token 裁剪
    estimated_tokens: int = 0    # 估算 token 数

    @property
    def char_count(self) -> int:
        """正文字符数（调试用）。"""
        return len(self.text)


def render_persona_prompt(
    persona: PersonaPreset,
    state_values: dict[str, str] | None = None,
    max_tokens: int | None = None,
) -> RenderedPrompt:
    """渲染单份人设预设的 System Prompt。

    参数:
        persona: 已加载的人设预设；
        state_values: 运行时状态变量（如 {"current_mood": "低落"}），
            缺失项将回退到注册表默认值（见 resolver 规则）；
        max_tokens: 可选 token 预算，超出则从尾部裁剪。
    """
    # 人设正文即当前唯一 Prompt 层；后续世界书等层在此拼接
    raw_layer = persona.prompt

    # 第 1 步：解析状态变量宏（层内文本仍可含宏）
    layer_text, warnings = resolve_template(raw_layer, state_values)

    # 第 2 步：token 预算裁剪（可选）
    truncated = False
    if max_tokens is not None:
        layer_text, truncated = truncate_to_budget(layer_text, max_tokens)

    return RenderedPrompt(
        persona=persona,
        text=layer_text,
        warnings=warnings,
        truncated=truncated,
        estimated_tokens=estimate_tokens(layer_text),
    )


#: 背景故事块在提示词里的标题（与「[记忆回忆]」等块标题同一套写法）
BACKGROUND_SECTION_TITLE = "[背景故事]"


def render_persona_background(
    persona: PersonaPreset,
    state_values: dict[str, str] | None = None,
) -> RenderedPrompt:
    """渲染角色卡的**背景故事**（用户自建角色才有；内置角色默认为空）。

    为什么与 `render_persona_prompt` 分开、而不直接拼进去：两者在提示词里的位置
    不同——人设正文是角色层的开头（身份与说话方式），背景故事是角色层的末尾
    （随后才是世界书、记忆等层）；且背景留空是常态，分开调用才能让调用方决定
    「要不要多这一块」。

    空背景返回 `text=""` 的 RenderedPrompt（调用方据此跳过拼接）。
    宏（`{{变量}}`）的解析与正文同源，因此背景里也能写 `{{user_name}}`。
    """
    if not persona.background.strip():
        return RenderedPrompt(persona=persona, text="")

    text, warnings = resolve_template(persona.background, state_values)
    section = f"{BACKGROUND_SECTION_TITLE}\n{text}" if text.strip() else ""
    return RenderedPrompt(
        persona=persona,
        text=section,
        warnings=warnings,
        estimated_tokens=estimate_tokens(section),
    )
