"""SSML 播报指令生成（魔珐星云 SDK 主路径）。

定位：项目当前围绕魔珐具身驱动 SDK 构建——
    后端产出「SSML 播报指令」→ 前端 `avatar.speak(ssml, is_start, is_end)`
    → SDK 内部完成 TTS + 口型 + 表情 + 动作 + 3D 渲染。

因此本模块只负责两件事：
1. 把 assistant 回复转成**可播报的 SSML**（含情绪 → KA 动作指令）；
2. 产出**字幕纯文本**与**播报元数据**（音色/语气/情绪），供前端展示与控制。

KA 指令格式（官方文档《具身驱动SDK（JS版本）接入说明》1.2.2）：
    <ue4event><type>ka</type><data><action_semantic>Hello</action_semantic></data></ue4event>

注：语气（tone）以**元数据**形式返回，不写入 SSML 正文——
避免 TTS 把括注内容念出来；语气实际由「文本内容（风格预设已控制）+ KA 动作 + 音色」共同体现。
"""

import re
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

# ---- 情绪 → KA 动作（自创映射，对接魔珐动作库语义）----
# KA 动作取值可在魔珐「具身驱动KA查询接口」中查询（见 ka_client.py）
EMOTION_KA_ACTIONS: dict[str, str] = {
    "anxious": "comfort",      # 焦虑 → 安抚
    "sad": "comfort",          # 难过 → 安抚
    "tired": "slow_down",      # 疲惫 → 放缓
    "angry": "calm_down",      # 生气 → 平复
    "happy": "Hello",          # 开心 → 打招呼式轻快动作
    "surprised": "Hello",
    "calm": "idle",
    "neutral": "idle",
}

# 情绪 → 语气描述（元数据，供前端/音色选择参考）
EMOTION_TONES: dict[str, str] = {
    "anxious": "柔声、放缓",
    "sad": "低沉、轻",
    "tired": "轻缓",
    "angry": "平稳",
    "happy": "轻快",
    "surprised": "稍快",
    "calm": "自然",
    "neutral": "自然",
}

# 情绪强度阈值：强度足够高时才附带 KA 动作（避免每句都做动作、显得浮夸）
KA_INTENSITY_THRESHOLD = 0.4

# SSML 标签（自研渲染路径剥离标签用）
_TAG_RE = re.compile(r"<[^>]+>")
# 动作指令块：必须先整体移除（否则其内部文本节点 kacomfort 会残留到字幕里）
_UE4EVENT_RE = re.compile(r"<ue4event>.*?</ue4event>", re.DOTALL)


@dataclass
class SpeakCommand:
    """一次播报指令（前端直接喂给 SDK 的 speak 方法）。"""

    ssml: str
    display_text: str                      # 字幕纯文本（已去标签）
    voice: str = ""
    emotion: str = "neutral"
    ka_action: str = ""                    # 实际写入 SSML 的 KA 动作（未达阈值时为空）
    tone: str = "自然"
    intensity: float = 0.5
    is_streaming: bool = False             # 是否按流式分段（大模型流式输出用）
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ssml": self.ssml,
            "display_text": self.display_text,
            "voice": self.voice,
            "emotion": self.emotion,
            "ka_action": self.ka_action,
            "tone": self.tone,
            "intensity": self.intensity,
            "is_streaming": self.is_streaming,
            "meta": self.meta,
        }


def build_ka_event(action_semantic: str) -> str:
    """生成 KA 动作指令片段。"""
    return (
        "<ue4event>"
        "<type>ka</type>"
        f"<data><action_semantic>{escape(action_semantic)}</action_semantic></data>"
        "</ue4event>"
    )


def build_ssml(text: str, ka_action: str = "") -> str:
    """把文本（可选 KA 动作）包装成 SSML。

    文本中的 XML 特殊字符会被转义，保证 SSML 结构不被破坏。
    """
    body = escape(text.strip())
    if ka_action:
        return f"<speak>{build_ka_event(ka_action)}{body}</speak>"
    return f"<speak>{body}</speak>"


def build_ssml_chunks(chunks: list[str], ka_action: str = "") -> list[str]:
    """把分段文本各包一层 SSML（分段播报用）。

    ⚠️ **每段必须是各自独立的完整 `<speak>` 文档** —— 这是实测结论，不要改成
    「同一个文档切成几片」：v0.1.2 曾依据 SDK 源码里
    `multi_turn_conversation_id` 只在 `is_end=true` 时自增这一点，推断
    「同一话轮的片段会被服务端拼起来解析」，于是把片段做成
    `<speak>chunk0` / `chunki` / `chunkN</speak>` 三形态。结果**数字人完全
    不发声** —— 说明服务端是**逐条解析**每个 `send_text` 的 `ssml` 的，
    缺 `<speak>` 包裹的片段直接被判为无效 SSML。已回退。

    由此也得到一个重要结论：魔珐路径下「分段播报」**做不到段间无缝** ——
    每段都是独立话轮，服务端逐段从零合成，段间必有合成空档。前端因此对
    魔珐路径**不做分段**，整段一次播报（见 `useChatSession` 的
    `fetchSpeechChunks` 调用条件），从根上避开段间空档。

    KA 动作只放首段：一句话内连续触发多次动作指令会让数字人反复抖动。
    纯空白段会被丢弃 —— 它既无音频也无字幕价值，且若撞上首段会把 KA 动作
    包进空段而完全丢失（见 `create_speak_command` 的过滤逻辑）。
    """
    cleaned = [chunk for chunk in (item.strip() for item in chunks) if chunk]
    return [
        build_ssml(chunk, ka_action if index == 0 else "")
        for index, chunk in enumerate(cleaned)
    ]


def strip_ssml(ssml: str) -> str:
    """从 SSML 中提取纯文本（字幕用）。

    注意：需先整体移除 <ue4event> 动作块，再去除剩余标签——
    否则块内的文本节点（如 ka / comfort）会残留到字幕中。
    """
    text = _UE4EVENT_RE.sub("", ssml)
    text = _TAG_RE.sub("", text)
    # 还原转义字符，得到可直接展示的文本
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


def resolve_ka_action(emotion: str | None, intensity: float) -> str:
    """按情绪与强度解析 KA 动作（未达阈值则不附加动作）。"""
    if not emotion or intensity < KA_INTENSITY_THRESHOLD:
        return ""
    return EMOTION_KA_ACTIONS.get(emotion, "")


def build_speak_command(
    text: str,
    *,
    emotion: str | None = None,
    intensity: float = 0.5,
    voice: str = "",
    is_streaming: bool = False,
    include_ka: bool = True,
) -> SpeakCommand:
    """文本 + 情绪 → 播报指令。

    参数:
        text: assistant 回复文本；
        emotion: 情绪标签（英文，来自 M4 情绪链路）；
        intensity: 情绪强度 0-1（决定是否附带 KA 动作）；
        voice: 魔珐音色 ID（tts_vcn）；
        is_streaming: 是否为流式分段播报（前端配合 is_start/is_end 使用）；
        include_ka: 是否允许附带 KA 动作（调试/测试可关闭）。
    """
    emotion_key = (emotion or "neutral").strip().lower()
    ka_action = resolve_ka_action(emotion_key, intensity) if include_ka else ""
    ssml = build_ssml(text, ka_action)

    return SpeakCommand(
        ssml=ssml,
        display_text=strip_ssml(ssml),
        voice=voice,
        emotion=emotion_key,
        ka_action=ka_action,
        tone=EMOTION_TONES.get(emotion_key, "自然"),
        intensity=intensity,
        is_streaming=is_streaming,
        meta={
            "has_ka": bool(ka_action),
            "ka_threshold": KA_INTENSITY_THRESHOLD,
            "note": "SSML 交前端 SDK 的 speak() 播报；语气由文本+KA+音色共同体现",
        },
    )


def split_for_streaming(text: str, max_chars: int = 40) -> list[str]:
    """把长文本按句切分成流式片段（前端按 is_start/is_end 逐段播报）。

    切分优先在句末标点处断开；过长的句子按字数硬切。
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return [text] if text else []

    sentences = re.findall(r"[^。！？；\n]*[。！？；\n]?", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(current) + len(sentence) <= max_chars:
            current += sentence
            continue
        if current:
            chunks.append(current)
            current = ""
        # 单句仍超长 → 硬切
        while len(sentence) > max_chars:
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        current = sentence
    if current:
        chunks.append(current)
    return chunks
