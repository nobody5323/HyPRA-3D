"""GPT-SoVITS 驱动的数字人数据实现（整段音频 + 估算口型，失败降级 local）。

与魔珐实现的区别（值得记住）：
- 魔珐 TTS 返回**字级时间戳**，口型与语音逐字对齐；
- GPT-SoVITS 只返回音频，所以走「按文本估算口型 → 按实际音频时长等比缩放」——
  对齐精度是**整段级**的（不逐字），但音频与口型**同源**，
  不会像「浏览器 TTS 出声 + 后端估算口型」那样结构性错位。

流程：
    文本 + 情绪 + 音色 id
      → GPT-SoVITS `/tts`：整段音频（wav）
      → 口型时间轴：估算轨道 → 缩放到音频实际时长
      → 表情 / 动作时间轴：emotion / expression + intensity（与 local / xmov 同一套）
      → 音频落盘 + AvatarOutput

失败策略：TTS 异常（服务未启动 / 超时 / 缺参考音频…）时**自动降级**到本地实现
（前端会看到 `has_audio=false`，可自行回落浏览器 TTS），不阻断对话。
"""

import hashlib
import logging
from pathlib import Path

from app.digital_human.base import DigitalHumanProvider
from app.digital_human.local_provider import (
    LocalDigitalHumanProvider,
    _build_body_track,
    _build_face_track,
)
from app.digital_human.models import AvatarOutput
from app.digital_human.viseme import (
    build_viseme_track_estimated,
    scale_track_ms,
    track_duration_ms,
)
from app.tools.emotion import EMOTION_FACIAL_EXPRESSIONS
from app.tts.base import TtsProvider
from app.tts.gpt_sovits import (
    DEFAULT_MEDIA_TYPE,
    GptSovitsClient,
    GptSovitsConfig,
    TtsAudio,
    VoiceRef,
)
from app.tts.gpt_sovits_provider import GptSovitsTtsProvider

# 缩放系数的合理区间。
#
# 为什么需要上下限：真实语音比"按字数估算"快或慢 15%~30% 都正常，但若音频时长
# 离谱（容器头损坏、服务返回了别的内容），不限幅会把口型拉长到几倍速或挤成一条线，
# 现场看起来就是"嘴乱动"。限幅后至少保持可用的近似对齐，并把原始系数写进 meta 便于排查。
MIN_SCALE = 0.25
MAX_SCALE = 4.0

logger = logging.getLogger(__name__)

# 无法判断时长时的口型来源标注
SOURCE_AUDIO = "audio"          # 已按真实音频时长缩放
SOURCE_ESTIMATED = "estimated"  # 拿不到时长（非 WAV / 空音频），保持估算


class GptSovitsDigitalHumanProvider(DigitalHumanProvider):
    """本地形象 + GPT-SoVITS 声音（带本地降级）。

    音频经 `TtsProvider` 接口取（§9.10 第 12 项）——本类不再直接依赖
    `GptSovitsClient`，将来换 TTS 只需换注入的 provider。
    """

    name = "gpt_sovits"

    def __init__(
        self,
        *,
        config: GptSovitsConfig | None = None,
        media_dir: str | Path = "media",
        fallback: DigitalHumanProvider | None = None,
        client: GptSovitsClient | None = None,
        tts: TtsProvider | None = None,
    ) -> None:
        """
        参数:
            config: GPT-SoVITS 配置（服务地址 / 默认音色 / 音色表）；缺省用默认值，
                此时调用会因"缺参考音频"而降级到 local —— 服务没部署时正是这个行为；
            media_dir: 音频落盘目录；
            fallback: 降级实现（默认本地）；
            client: 注入的 GPT-SoVITS 客户端（测试用；缺省按 config 构造）；
            tts: 注入的 TTS provider（优先于 client；换其他 TTS 时用）。
        """
        self.config = config or GptSovitsConfig()
        self.media_dir = Path(media_dir)
        self._fallback = fallback or LocalDigitalHumanProvider()
        self._tts = tts or GptSovitsTtsProvider(self.config, client=client)

    def synthesize(
        self,
        text: str,
        *,
        emotion: str | None = None,
        expression: str | None = None,
        intensity: float = 0.5,
        voice: str | None = None,
    ) -> AvatarOutput:
        voice_ref, voice_label, unknown_voice, voice_source = self._resolve_voice(voice, emotion)

        # ① 先备好估算口型：即使 TTS 失败，降级路径也直接复用本地实现（无需重算）
        visemes = build_viseme_track_estimated(text)
        estimated_ms = track_duration_ms(visemes)

        try:
            audio = self._tts.synthesize(
                text,
                ref_audio_path=voice_ref.ref_audio_path,
                prompt_text=voice_ref.prompt_text,
                prompt_lang=voice_ref.prompt_lang,
            )
        except Exception as exc:  # 网络 / 鉴权 / 参数 / 缺参考音频等 → 降级
            degraded = self._fallback.synthesize(
                text, emotion=emotion, expression=expression, intensity=intensity
            )
            degraded.meta.update(
                {
                    "degraded_from": self.name,
                    "degrade_reason": f"{type(exc).__name__}: {exc}",
                    "voice": voice_label,
                }
            )
            return degraded

        # ② 口型：估算轨道 → 缩放到音频实际时长（拿不到时长就保持估算）
        audio_ms = int(round(audio.duration_s * 1000)) if audio.has_duration else 0
        scale = 1.0
        duration_source = SOURCE_ESTIMATED
        if audio_ms > 0 and estimated_ms > 0:
            scale = _clamp_scale(audio_ms / estimated_ms)
            visemes = scale_track_ms(visemes, int(round(estimated_ms * scale)))
            duration_source = SOURCE_AUDIO
        duration_ms = track_duration_ms(visemes) or audio_ms

        # ③ 表情与动作（与 local / xmov 完全同一套规则，保证渲染侧体感一致）
        resolved_expression = expression or EMOTION_FACIAL_EXPRESSIONS.get(
            emotion or "", "default"
        )
        face = _build_face_track(duration_ms, resolved_expression, intensity)
        body = _build_body_track(duration_ms, intensity)

        # ④ 音频落盘（供前端按 URL 播放）
        audio_path = self._save_audio(audio)

        meta = {
            "voice": voice_label,
            # 这次声音是谁选的（request 显式 / emotion 按情绪 / default / config）
            "voice_source": voice_source,
            "emotion": emotion or "neutral",
            "expression": resolved_expression,
            "intensity": intensity,
            "audio_bytes": len(audio.audio_bytes),
            "audio_duration_ms": audio_ms,
            "estimated_ms": estimated_ms,
            "scale": round(scale, 4),
            "duration_source": duration_source,
            # 口型精度自述：前端/评审不必去猜这条时间轴有多准
            "align": "estimated-scaled",
        }
        if unknown_voice:
            # 不静默吞掉：前端传了音色表里没有的 id 时，这条 meta 是唯一线索
            meta["unknown_voice"] = unknown_voice

        return AvatarOutput(
            text=text,
            provider=self.name,
            duration_ms=duration_ms,
            visemes=visemes,
            face=face,
            body=body,
            audio_path=str(audio_path) if audio_path else None,
            audio_format=audio.audio_format or DEFAULT_MEDIA_TYPE,
            meta=meta,
        )

    def warmup(self, text: str = "预热。") -> bool:
        """预热一次（丢弃音频），让服务端模型/显存进入稳定状态。

        为什么要它：实测服务刚可用的首个请求明显偏慢（显存分配、算子选择等一次性开销），
        启动时在后台预热一次，用户听到的第一句就不吃这个亏。

        失败**只记日志**：预热是优化，不是启动前置条件（服务没起来也必须能启动）。
        """
        voice_ref, _, _, _ = self._resolve_voice(None, None)
        try:
            self._tts.synthesize(
                text,
                ref_audio_path=voice_ref.ref_audio_path,
                prompt_text=voice_ref.prompt_text,
                prompt_lang=voice_ref.prompt_lang,
            )
        except Exception as exc:
            logger.warning("GPT-SoVITS 预热失败（不影响使用）：%s", exc)
            return False
        logger.info("GPT-SoVITS 预热完成")
        return True

    def _resolve_voice(
        self, voice: str | None, emotion: str | None = None
    ) -> tuple[VoiceRef, str, str | None, str]:
        """把「请求里的音色 id + 本轮情绪」解析成参考音频。

        优先级（刻意如此，避免「声音怎么变了」无法解释）：
        1. **显式 voice**：用户/前端明确指定就用它；
        2. **情绪映射**：仅当**没传** voice 时生效（音色表 `_emotion_map`）——
           即「用户难过时听起来也是难过的语气」；
        3. **default_voice_id**；
        4. 都没配 → 用配置里的 ref_audio_path（最终会因缺参考音频而降级）。

        显式传了未知 voice 时**不**再走情绪映射，而是直接回落默认音色：
        拼错就应表现为可预测的“没换成”，而不是“接心情换了个声音”——后者难排查。

        返回:
            (音色, 生效的音色标识, 未命中的标识或 None, 来源 request|emotion|default|config)
        """
        requested = (voice or "").strip()
        if requested and requested in self.config.voices:
            return self.config.voices[requested], requested, None, "request"

        if not requested:
            mapped = self.config.emotion_voices.get((emotion or "").strip().lower(), "")
            if mapped and mapped in self.config.voices:
                return self.config.voices[mapped], mapped, None, "emotion"

        default_id = self.config.default_voice_id.strip()
        if default_id and default_id in self.config.voices:
            return self.config.voices[default_id], default_id, requested or None, "default"

        fallback = VoiceRef(
            ref_audio_path=self.config.ref_audio_path,
            prompt_text=self.config.prompt_text,
            prompt_lang=self.config.prompt_lang,
            label=default_id,
        )
        return fallback, default_id, requested or None, "config"

    def _save_audio(self, audio: TtsAudio) -> Path | None:
        """音频落盘，返回路径（无音频字节时返回 None）。

        命名用**音频内容的摘要**（而不是文本摘要）：GPT-SoVITS 下同一句话换个音色
        内容就不同，若按文本命名会互相覆盖——而前端可能正在播放上一个文件，
        覆盖会让声音在半路变人。内容寻址天然避免这个问题，同内容还能复用。
        """
        if not audio.audio_bytes:
            return None
        self.media_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.md5(audio.audio_bytes).hexdigest()[:12]
        path = self.media_dir / f"tts_{digest}.{audio.audio_format or DEFAULT_MEDIA_TYPE}"
        path.write_bytes(audio.audio_bytes)
        return path


def _clamp_scale(scale: float) -> float:
    """把缩放系数限制在 MIN_SCALE~MAX_SCALE（见常量处的说明）。"""
    return max(MIN_SCALE, min(MAX_SCALE, scale))


__all__ = [
    "GptSovitsDigitalHumanProvider",
    "MAX_SCALE",
    "MIN_SCALE",
    "SOURCE_AUDIO",
    "SOURCE_ESTIMATED",
]
