"""音色表加载：JSON 文件 → 音色清单 + 情绪→音色映射。

为什么用文件而不是 .env：
「音色 id → 参考音频 + 参考文本」是**结构化**数据，塞进环境变量会变成一行要转义的
JSON（Windows 的 .env 里尤其难维护）。本项目对同类「用户本地结构化配置」
（MCP 清单、ST 预设）一律走文件：`GPT_SOVITS_VOICES_FILE` 指向一个 JSON，
**文件不存在 = 未配置**（只有默认音色），这是默认状态而非错误。

文件格式（普通键 = 音色 id，即前端 `voice` 字段要传的值）::

    {
      "gentle": {
        "ref_audio_path": "refs/gentle.wav",
        "prompt_text": "今天也辛苦了。",
        "prompt_lang": "zh",
        "label": "温柔"
      },
      "_emotion_map": { "sad": "gentle", "happy": "lively" }
    }

`_` 开头的键是**保留键**（不会被当作音色），目前只有一个 `_emotion_map`：
本轮情绪标签（后端 8 类）→ 音色 id，用于「不指定 voice 时按情绪选声音」。

容错原则：音色表坏了**不能让后端启动失败**——最坏情况退回默认音色，对话照常。
所以单条结构不对就丢弃单条、整表解析不了就丢弃整表，并各记一条 warning
（静默丢弃最难查）。
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.tts.gpt_sovits import DEFAULT_PROMPT_LANG, VoiceRef

logger = logging.getLogger(__name__)

#: 情绪→音色映射的保留键（`_` 前缀均视为保留，不当作音色 id）
EMOTION_MAP_KEY = "_emotion_map"


@dataclass(frozen=True)
class VoiceTable:
    """音色表：可选音色 + 情绪→音色映射。"""

    voices: dict[str, VoiceRef] = field(default_factory=dict)
    #: 情绪标签（如 sad）→ 音色 id（必须在 voices 里，加载时已校验）
    emotion_voices: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.voices)


def load_voice_config(path: str | Path | None) -> VoiceTable:
    """从 JSON 文件读音色表（含情绪映射）；文件不存在或不可用时返回空表。

    参数:
        path: JSON 文件路径（相对 backend 运行目录）；空值 = 未配置。
    """
    if not path:
        return VoiceTable()
    target = Path(path)
    if not target.is_file():
        return VoiceTable()   # 未配置：默认状态，不记日志，避免刷屏

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("音色表读取失败，已忽略（%s）：%s", target, exc)
        return VoiceTable()

    if not isinstance(raw, dict):
        logger.warning("音色表格式应为 {音色id: {...}}，已忽略：%s", target)
        return VoiceTable()

    voices: dict[str, VoiceRef] = {}
    for voice_id, entry in raw.items():
        key = str(voice_id).strip()
        if key.startswith("_"):
            continue   # 保留键：不是音色
        parsed = _parse_voice(key, entry)
        if parsed is not None:
            voices[key] = parsed

    return VoiceTable(
        voices=voices,
        emotion_voices=_parse_emotion_map(raw.get(EMOTION_MAP_KEY), voices),
    )


def load_voice_table(path: str | Path | None) -> dict[str, VoiceRef]:
    """只要音色清单的便捷入口（等价 `load_voice_config(path).voices`）。"""
    return load_voice_config(path).voices


def _parse_emotion_map(raw: object, voices: dict[str, VoiceRef]) -> dict[str, str]:
    """解析情绪→音色映射；指向不存在音色的条目直接丢弃（否则是「选了没声音」的坑）。"""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning("音色表的 %s 应为 {情绪: 音色id}，已忽略", EMOTION_MAP_KEY)
        return {}

    mapping: dict[str, str] = {}
    for emotion, voice_id in raw.items():
        key = str(emotion).strip().lower()
        target = str(voice_id or "").strip()
        if not key or not target:
            continue
        if target not in voices:
            logger.warning(
                "情绪映射「%s → %s」指向不存在的音色，已跳过（可用音色：%s）",
                key, target, ", ".join(sorted(voices)) or "无",
            )
            continue
        mapping[key] = target
    return mapping


def _parse_voice(voice_id: str, entry: object) -> VoiceRef | None:
    """解析单条音色；结构不合法时记 warning 并返回 None（该条作废）。"""
    key = voice_id.strip()
    if not key:
        logger.warning("音色表存在空 id，已跳过")
        return None
    if not isinstance(entry, dict):
        logger.warning("音色「%s」的配置不是对象，已跳过", key)
        return None

    ref_audio_path = str(entry.get("ref_audio_path") or "").strip()
    if not ref_audio_path:
        # 没有参考音频就没有「音色」可言：GPT-SoVITS 靠它克隆音色
        logger.warning("音色「%s」缺少 ref_audio_path，已跳过", key)
        return None

    return VoiceRef(
        ref_audio_path=ref_audio_path,
        prompt_text=str(entry.get("prompt_text") or ""),
        prompt_lang=str(entry.get("prompt_lang") or DEFAULT_PROMPT_LANG),
        label=str(entry.get("label") or "").strip(),
    )
