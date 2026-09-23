"""GPT-SoVITS 客户端：文本 → 音频字节。

对接官方 `api_v2.py`（默认 `http://127.0.0.1:9880`）主接口：

    POST /tts
    { "text": "...", "text_lang": "zh",
      "ref_audio_path": "...", "prompt_text": "...", "prompt_lang": "zh",
      "media_type": "wav", "speed_factor": 1.0, "text_split_method": "cut5" }
    → 响应体是**裸音频字节**（容器格式由 media_type 决定，不是 JSON）

关键概念：**音色 = 参考音频**
    GPT-SoVITS 是零样本音色克隆——没有服务端预置的音色 ID，
    「换个声音」= 换一段参考音频 + 这段音频对应的文字（prompt_text/prompt_lang）。
    所以本客户端把参考音频当作参数而非固定配置，便于上层按陪伴对象切音色。

为什么用类而不是模块级函数（对比 `app/digital_human/xmov_tts.py`）：
    这里要携带一组「服务地址 + 参考音频 + 语言 + 语速」配置，且需要能注入
    httpx transport 让单测**不联网**；函数式会让这组参数在每层重复传递。

**重要限制**：`/tts` 不返回字级时间戳（官方字幕能力只到句子级），
因此口型只能按「估算轨道 + 实际音频时长缩放」对齐，
详见 `app/digital_human/gpt_sovits_provider.py`。
"""

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.tts.audio import probe_audio_duration_s
from app.tts.base import DEFAULT_MEDIA_TYPE, TtsAudio, TtsError

logger = logging.getLogger(__name__)

# 默认端点与参数（与官方 api_v2.py 的默认启动方式一致）
DEFAULT_BASE_URL = "http://127.0.0.1:9880"
DEFAULT_TEXT_LANG = "zh"
DEFAULT_PROMPT_LANG = "zh"
DEFAULT_SPEED = 1.0
TTS_PATH = "/tts"

# api_v2 支持的输出容器（raw = 裸 PCM，无容器头，拿不到时长）
ALLOWED_MEDIA_TYPES = frozenset({"wav", "ogg", "aac", "raw"})

#: 请求体里**不允许**被额外参数覆盖的字段。
#:
#: 理由：这些字段决定「说什么 / 用什么声音 / 输出什么格式」——真的被覆盖就是
#: 「传了 A 却合成出 B」，属于最难查的一类问题；而调优参数（采样 / 并行等）完全开放。
PROTECTED_BODY_KEYS = frozenset(
    {
        "text", "text_lang", "ref_audio_path", "prompt_text", "prompt_lang",
        "media_type", "streaming_mode", "text_split_method", "speed_factor",
    }
)

# 错误信息里回显响应正文的最大长度（服务端报错可能很长，且可能含路径）
_ERROR_BODY_MAX_CHARS = 200


class GptSovitsError(TtsError):
    """GPT-SoVITS 调用失败（服务未启动 / 超时 / 参数被拒 / 空音频等）。

    继承 `TtsError`：调用方只需 `except TtsError` 就能覆盖所有 TTS 实现，
    不必逐个记住各家的异常类名。降级逻辑见 `gpt_sovits_provider.py`。
    """


@dataclass(frozen=True)
class VoiceRef:
    """一个 GPT-SoVITS 音色 = 一段参考音频 + 该音频对应的文字。

    GPT-SoVITS 没有服务端预置的音色 ID：零样本克隆里「音色」就是这两样东西，
    所以配置用「id → VoiceRef」的表来表达「有哪些声音可选」。
    """

    ref_audio_path: str
    prompt_text: str = ""
    prompt_lang: str = DEFAULT_PROMPT_LANG
    #: 界面展示名（可选；缺省时前端直接显示 id）
    label: str = ""


@dataclass
class GptSovitsConfig:
    """GPT-SoVITS 相关配置（由 `app/config.py` 的 Settings 组装，见 digital_human/factory.py）。

    字段与 `GptSovitsClient` 基本对应，额外带**音色表** `voices`：
    驱动层据此把请求里的 `voice` id 解析成具体的参考音频。
    """

    base_url: str = DEFAULT_BASE_URL
    ref_audio_path: str = ""      # 默认音色的参考音频（**服务端**可见路径）
    prompt_text: str = ""         # 默认音色的参考文本
    prompt_lang: str = DEFAULT_PROMPT_LANG
    text_lang: str = DEFAULT_TEXT_LANG
    speed: float = DEFAULT_SPEED
    media_type: str = DEFAULT_MEDIA_TYPE   # 建议 wav：浏览器 <audio> 可直接播放
    timeout: float = 60.0
    default_voice_id: str = ""    # 空 = 直接用上面的 ref_audio_path
    voices: Mapping[str, VoiceRef] = field(default_factory=dict)
    #: 情绪标签（sad / happy…）→ 音色 id：请求未指定 voice 时按本轮情绪选声音
    emotion_voices: Mapping[str, str] = field(default_factory=dict)
    #: 额外请求参数，原样透传给 api_v2 的 /tts（例 {"parallel_infer": False}）；
    #: 核心字段不可覆盖，见 PROTECTED_BODY_KEYS
    extra_params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """归一化情绪映射的键（大小写 / 空白）。

        为什么放在配置对象而不是只在音色表加载器里：配置可能被**直接构造**
        （测试、其它调用方），如果只有加载器做归一化，写成 "SAD" 就会静默失效——
        而“情绪映射不生效”是最难查的一类问题（声音听起来“也挺正常”）。
        """
        self.emotion_voices = {
            str(key).strip().lower(): str(value).strip()
            for key, value in self.emotion_voices.items()
            if str(key).strip() and str(value).strip()
        }


class GptSovitsClient:
    """GPT-SoVITS `api_v2.py` 客户端。"""
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        ref_audio_path: str = "",
        prompt_text: str = "",
        prompt_lang: str = DEFAULT_PROMPT_LANG,
        text_lang: str = DEFAULT_TEXT_LANG,
        speed: float = DEFAULT_SPEED,
        media_type: str = DEFAULT_MEDIA_TYPE,
        text_split_method: str = "cut5",
        timeout: float = 60.0,
        extra_params: Mapping[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        参数:
            base_url: 服务地址，如 http://127.0.0.1:9880；
            ref_audio_path: 参考音频路径（**服务端**可见的路径，即参考音频要放在跑
                GPT-SoVITS 的那台机器上）；空值表示"尚未配置"，调用时才报错，
                这样服务没部署时构造客户端不会炸。
            prompt_text: 参考音频对应的文字（提升音色相似度，可留空）；
            prompt_lang: 参考音频语言；
            text_lang: 待合成文本语言；
            speed: 语速倍率（1.0 = 原速）；
            media_type: 输出容器（wav / ogg / aac / raw）；
            text_split_method: 官方切句策略，默认 cut5；
            timeout: 单次合成超时（秒）；
            extra_params: 额外透传参数（调优用）；含核心字段的键会被忽略并记 warning；
            transport: 仅测试用（注入 httpx.MockTransport 即可不联网）。
        """
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.ref_audio_path = ref_audio_path or ""
        self.prompt_text = prompt_text or ""
        self.prompt_lang = prompt_lang or DEFAULT_PROMPT_LANG
        self.text_lang = text_lang or DEFAULT_TEXT_LANG
        self.speed = speed
        self.media_type = media_type or DEFAULT_MEDIA_TYPE
        self.text_split_method = text_split_method
        self.timeout = timeout
        self._transport = transport
        self.extra_params = _filter_extra_params(extra_params)

    async def synthesize(
        self,
        text: str,
        *,
        ref_audio_path: str | None = None,
        prompt_text: str | None = None,
        prompt_lang: str | None = None,
        text_lang: str | None = None,
        speed: float | None = None,
        media_type: str | None = None,
    ) -> TtsAudio:
        """合成一段语音（按需覆盖音色相关的参考音频与语言）。

        参数:
            text: 要合成的文本；
            ref_audio_path / prompt_text: 覆盖默认音色（按陪伴对象切音色时用）；
            其余参数: 覆盖本次合成的语言 / 语速 / 容器格式。

        异常:
            GptSovitsError: 参数不合法、连接失败、超时、服务端报错或返回空音频。
        """
        payload_text = (text or "").strip()
        if not payload_text:
            raise GptSovitsError("文本为空，不调用 TTS")

        ref = (self.ref_audio_path if ref_audio_path is None else ref_audio_path).strip()
        if not ref:
            raise GptSovitsError(
                "缺少参考音频 ref_audio_path——GPT-SoVITS 用参考音频决定音色"
                "（请在 backend/.env 的 GPT_SOVITS_REF_AUDIO 或音色表里配置）"
            )

        fmt = (media_type or self.media_type).strip().lower()
        if fmt not in ALLOWED_MEDIA_TYPES:
            raise GptSovitsError(
                f"不支持的 media_type：{fmt!r}（可选 {'/'.join(sorted(ALLOWED_MEDIA_TYPES))}）"
            )

        prompt = (self.prompt_text if prompt_text is None else prompt_text).strip()
        body = {
            "text": payload_text,
            "text_lang": (text_lang or self.text_lang).strip(),
            "ref_audio_path": ref,
            "prompt_text": prompt,
            "prompt_lang": (prompt_lang or self.prompt_lang).strip(),
            "text_split_method": self.text_split_method,
            "media_type": fmt,
            "speed_factor": float(self.speed if speed is None else speed),
            "streaming_mode": False,   # 一次取回完整音频，便于按总时长对齐口型
        }
        # 额外参数最后合并：调优项可覆盖默认值，但核心字段已在构造时滤掉
        body.update(self.extra_params)

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self.base_url}{TTS_PATH}",
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.TimeoutException as exc:
            raise GptSovitsError(
                f"请求 GPT-SoVITS 超时（{self.timeout}s）：{self.base_url}"
            ) from exc
        except httpx.HTTPError as exc:
            # 连不上 / 连接被拒：绝大多数情况是服务没启动
            raise GptSovitsError(
                f"无法连接 GPT-SoVITS（{self.base_url}）：{exc}"
            ) from exc

        if response.status_code >= 400:
            raise GptSovitsError(
                f"GPT-SoVITS 返回 HTTP {response.status_code}：{_body_snippet(response)}"
            )

        audio = response.content
        if not audio:
            raise GptSovitsError("GPT-SoVITS 返回了空音频")

        return TtsAudio(
            audio_bytes=audio,
            audio_format=fmt,
            duration_s=probe_audio_duration_s(audio, fmt),
        )

    def synthesize_sync(self, text: str, **overrides) -> TtsAudio:
        """同步封装（供同步调用方使用；**不要在事件循环内调用**）。"""
        return asyncio.run(self.synthesize(text, **overrides))


def parse_extra_params(raw: str | None) -> dict[str, Any]:
    """把配置里的一行 JSON 解析成额外参数表（.env → 驱动配置，容错优先）。

    - 空值 → 空表（未配置，全用服务端默认值）；

    - 非法 JSON / 顶层不是对象 → 空表 + warning（一个拼写错误不该弄挂后端）。

    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("GPT_SOVITS_EXTRA_PARAMS 不是合法 JSON，已忽略：%s", exc)
        return {}
    if not isinstance(parsed, dict):
        logger.warning("GPT_SOVITS_EXTRA_PARAMS 应为 JSON 对象，已忽略：%r", parsed)
        return {}
    return parsed


def _filter_extra_params(extra: Mapping[str, Any] | None) -> dict[str, Any]:
    """滤掉试图覆盖核心字段的键（并记 warning）：拼错也不至于合成出别的内容。"""
    if not extra:
        return {}
    filtered: dict[str, Any] = {}
    for key, value in extra.items():
        name = str(key)
        if name in PROTECTED_BODY_KEYS:
            logger.warning("额外参数不能覆盖核心字段，已忽略：%s", name)
            continue
        filtered[name] = value
    return filtered


def _body_snippet(response: httpx.Response) -> str:
    """截取响应正文用于报错（过长只留前 200 字，避免刷屏）。"""
    try:
        text = response.text.strip()
    except Exception:  # 正文不是文本（理论上不会走到）
        return "(无法解析响应正文)"
    if not text:
        return "(空响应正文)"
    return text[:_ERROR_BODY_MAX_CHARS]
