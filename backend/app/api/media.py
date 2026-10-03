"""media 路由：数字人驱动与播报指令。

主路径（魔珐 SDK）：
    POST /media/speak     文本 + 情绪 → SSML 播报指令（前端 avatar.speak(ssml)）

扩展路径（自研/通用渲染，可接入任意 3D/2D 模型）：
    POST /media/avatar    文本 + 情绪 → 口型/表情/动作时间轴 + 可选音频
    GET  /media/audio/{f} 读取已生成的音频文件（供前端播放）
    GET  /media/tts/voices 当前语音引擎与可选音色（前端据此决定用服务端音频还是浏览器 TTS）

设计：/media/* 为**同步**路由（FastAPI 放入线程池执行），
因此 provider 内部可用 asyncio.run 调用魔珐 WebSocket / GPT-SoVITS HTTP。
"""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.digital_human.base import DigitalHumanProvider
from app.digital_human.factory import create_digital_human_provider
from app.digital_human.gpt_sovits_provider import GptSovitsDigitalHumanProvider
from app.digital_human.ssml import (
    build_speak_command,
    build_streaming_ssml_chunks,
    split_for_streaming,
)
from app.tts.gpt_sovits import GptSovitsConfig, parse_extra_params
from app.tts.voices import load_voice_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])

# 进程内单例（测试可经 set_digital_human_provider 注入）
_provider: DigitalHumanProvider | None = None

#: 数字人驱动单例**快照**了这些配置（字名与 `Settings` 字段一致）。
#: 用途：管理 API（`PUT /plugins/{id}/settings`）判断保存后要不要重建它——
#: 其中 `gpt_sovits_*` 那一段已可经「能力中心 → 语音合成」在界面上改。
DIGITAL_HUMAN_CONFIG_FIELDS: tuple[str, ...] = (
    "digital_human_provider",
    "xmov_app_id",
    "xmov_secret",
    "xmov_voice",
    "xmov_host",
    "media_dir",
    "gpt_sovits_base_url",
    "gpt_sovits_ref_audio",
    "gpt_sovits_prompt_text",
    "gpt_sovits_prompt_lang",
    "gpt_sovits_text_lang",
    "gpt_sovits_speed",
    "gpt_sovits_media_type",
    "gpt_sovits_timeout",
    "gpt_sovits_extra_params",
    "gpt_sovits_voices_file",
    "gpt_sovits_default_voice",
    "gpt_sovits_warmup",
)

# 允许的音频后缀（防目录穿越 + 限制类型）。
# 刻意**不**包含 .raw：GPT-SoVITS 的 media_type=raw 是裸 PCM，浏览器 <audio> 放不了，
# 就算能取到也没用（PCM 之所以在列表里是魔珐流式返回的历史惯性）。
_ALLOWED_AUDIO_SUFFIXES = {".pcm", ".mp3", ".wav", ".ogg", ".m4a", ".aac"}


def _build_gpt_sovits_config(settings) -> GptSovitsConfig:
    """把 Settings 组装成驱动层要的配置（含音色表与情绪→音色映射）。"""
    table = load_voice_config(settings.gpt_sovits_voices_file)
    return GptSovitsConfig(
        base_url=settings.gpt_sovits_base_url,
        ref_audio_path=settings.gpt_sovits_ref_audio,
        prompt_text=settings.gpt_sovits_prompt_text,
        prompt_lang=settings.gpt_sovits_prompt_lang,
        text_lang=settings.gpt_sovits_text_lang,
        speed=settings.gpt_sovits_speed,
        media_type=settings.gpt_sovits_media_type,
        timeout=settings.gpt_sovits_timeout,
        default_voice_id=settings.gpt_sovits_default_voice,
        voices=table.voices,
        emotion_voices=table.emotion_voices,
        extra_params=parse_extra_params(settings.gpt_sovits_extra_params),
    )


def get_digital_human_provider() -> DigitalHumanProvider:
    """懒加载数字人 provider（按 .env 配置）。"""
    global _provider
    if _provider is None:
        settings = get_settings()
        _provider = create_digital_human_provider(
            settings.digital_human_provider,
            app_id=settings.xmov_app_id,
            secret=settings.xmov_secret,
            voice=settings.xmov_voice,
            host=settings.xmov_host,
            media_dir=settings.media_dir,
            gpt_sovits=_build_gpt_sovits_config(settings),
        )
    return _provider


def warmup_digital_human_provider() -> bool:
    """预热当前数字人驱动（若它支持预热）；失败只记日志。

    供应用启动时在后台调用：见 `app/main.py` 的 lifespan。

    """
    try:
        provider = get_digital_human_provider()
    except Exception as exc:   # 例：配了 xmov 但没填密钥
        logger.warning("数字人驱动构建失败，跳过预热：%s", exc)
        return False
    warmup = getattr(provider, "warmup", None)
    if not callable(warmup):
        return False   # 该驱动没有预热能力（local / xmov）：不是错误
    try:
        return bool(warmup())
    except Exception as exc:
        logger.warning("预热失败（已忽略）：%s", exc)
        return False


def set_digital_human_provider(provider: DigitalHumanProvider | None) -> None:
    """替换/重置 provider（测试与运行时切换用）。"""
    global _provider
    _provider = provider


class SpeakRequest(BaseModel):
    """播报指令请求（魔珐 SDK 主路径）。"""

    text: str = Field(min_length=1, description="要播报的文本（通常是 assistant 回复）")
    emotion: str | None = Field(default=None, description="情绪标签（英文，如 anxious）")
    intensity: float = Field(default=0.5, ge=0.0, le=1.0, description="情绪强度 0-1")
    voice: str | None = Field(default=None, description="魔珐音色 ID（tts_vcn）")
    streaming: bool = Field(default=False, description="是否返回流式分段（配合 is_start/is_end）")
    max_chars: int = Field(default=40, ge=10, le=200, description="流式分段的字数上限")


class SpeakResponse(BaseModel):
    """播报指令（直接喂给前端 SDK）。"""

    ssml: str = Field(description="SSML 播报文本（含 KA 动作指令）")
    display_text: str = Field(description="字幕纯文本（已去标签）")
    voice: str
    emotion: str
    ka_action: str = Field(description="实际写入的动作标识（未达强度阈值时为空）")
    tone: str = Field(description="语气描述（元数据，供前端展示）")
    intensity: float
    chunks: list[str] = Field(
        default_factory=list,
        description="流式分段纯文本（streaming=true 时）；字幕按段推进",
    )
    ssml_chunks: list[str] = Field(
        default_factory=list,
        description=(
            "与 chunks 一一对应的 SSML 片段（streaming=true 时）；"
            "它们是**同一个 <speak> 文档**的若干部分，前端须按"
            "「首段 is_start / 末段 is_end」作为同一次播报喂给 SDK"
        ),
    )
    meta: dict = Field(default_factory=dict)


@router.post("/speak", response_model=SpeakResponse)
def create_speak_command(req: SpeakRequest) -> SpeakResponse:
    """文本 + 情绪 → SSML 播报指令（魔珐 SDK 主路径）。

    前端用法：
        const cmd = await fetch('/media/speak', {...}).then(r => r.json());
        avatar.speak(cmd.ssml, true, true);
    """
    settings = get_settings()
    command = build_speak_command(
        req.text,
        emotion=req.emotion,
        intensity=req.intensity,
        voice=req.voice or settings.xmov_voice,
        is_streaming=req.streaming,
    )
    chunks = split_for_streaming(req.text, req.max_chars) if req.streaming else []
    # 句切分会产生**纯空白段**（例如换行被单独切成一段）：空段既无音频也无字幕价值，
    # 而且若它撞上首段，KA 动作会被包进空段从而完全丢失（`<speak></speak>`）。
    if chunks:
        chunks = [chunk for chunk in chunks if chunk.strip()]
    # 流式片段是**同一个 <speak> 文档**的若干部分，不是各自独立的文档：
    # 前端按「首段 is_start / 末段 is_end」把它们作为**同一次播报**喂给 SDK，
    # 服务端会把同一话轮的片段拼起来解析——若每段各自包一层 <speak>，
    # 拼出来就是多个根节点（非法 XML）。
    # 转义与 KA 事件结构仍由后端负责（前端不自行拼标签）；KA 动作只放首段。
    ssml_chunks = build_streaming_ssml_chunks(chunks, command.ka_action)

    return SpeakResponse(
        ssml=command.ssml,
        display_text=command.display_text,
        voice=command.voice,
        emotion=command.emotion,
        ka_action=command.ka_action,
        tone=command.tone,
        intensity=command.intensity,
        chunks=chunks,
        ssml_chunks=ssml_chunks,
        meta=command.meta,
    )


class AvatarRequest(BaseModel):
    """数字人驱动请求。"""

    text: str = Field(min_length=1, description="要合成的文本（通常是 assistant 回复）")
    emotion: str | None = Field(default=None, description="情绪标签（英文，如 anxious）")
    intensity: float = Field(default=0.5, ge=0.0, le=1.0, description="情绪强度 0-1")
    voice: str | None = Field(
        default=None,
        description="音色（xmov 为 tts_vcn；gpt_sovits 为音色表 id，见 GET /media/tts/voices）",
    )


class AvatarResponse(BaseModel):
    """数字人驱动数据（时间轴 + 音频）。"""

    text: str
    provider: str
    duration_ms: int
    has_audio: bool
    audio_url: str | None = Field(default=None, description="音频访问地址（若有）")
    audio_base64: str | None = None
    audio_format: str
    visemes: list[dict] = Field(default_factory=list, description="口型时间轴")
    face: list[dict] = Field(default_factory=list, description="表情时间轴")
    body: list[dict] = Field(default_factory=list, description="动作时间轴")
    meta: dict = Field(default_factory=dict)


@router.post("/avatar", response_model=AvatarResponse)
def create_avatar(req: AvatarRequest) -> AvatarResponse:
    """文本 + 情绪 → 数字人驱动数据。"""
    settings = get_settings()
    if not settings.avatar_enabled:
        raise HTTPException(status_code=503, detail="数字人驱动已关闭（AVATAR_ENABLED=false）")

    output = get_digital_human_provider().synthesize(
        req.text,
        emotion=req.emotion,
        intensity=req.intensity,
        voice=req.voice,
    )
    payload = output.to_dict()

    audio_url = None
    if output.audio_path:
        audio_url = f"/media/audio/{Path(output.audio_path).name}"

    return AvatarResponse(
        text=output.text,
        provider=output.provider,
        duration_ms=output.duration_ms,
        has_audio=output.has_audio,
        audio_url=audio_url,
        audio_base64=output.audio_base64,
        audio_format=output.audio_format,
        visemes=payload["visemes"],
        face=payload["face"],
        body=payload["body"],
        meta=output.meta,
    )


class TtsVoiceInfo(BaseModel):
    """一个可选音色。

    刻意**不**回显参考音频路径：那是**服务端**的文件路径，
    前端只需要 id 与展示名（少暴露一条服务端结构信息）。
    """

    id: str = Field(description="音色 id（请求 /media/avatar 时填在 voice 字段）")
    label: str = Field(description="展示名（音色表未填 label 时等于 id）")
    is_default: bool = Field(default=False, description="是否为未指定 voice 时的默认音色")


class TtsVoicesResponse(BaseModel):
    """语音引擎状态 + 音色清单（前端据此决定用服务端音频还是**静默**）。"""

    provider: str = Field(description="当前数字人驱动：local | xmov | gpt_sovits")
    server_tts: bool = Field(
        description="该驱动是否产出服务端音频（false = 前端不播报语音，只显示文字）"
    )
    configured: bool = Field(description="服务端 TTS 是否已配好（缺配置时会降级为无音频）")
    default_voice: str = Field(default="", description="默认音色 id（空 = 用配置里的默认参考音频）")
    voices: list[TtsVoiceInfo] = Field(default_factory=list)
    emotion_voices: bool = Field(
        default=False,
        description="是否配了「情绪→音色」自动映射（前端据此说明不选音色时的行为）",
    )
    note: str = Field(default="", description="状态说明 / 不可用原因（可直接展示给用户）")


# 非 gpt_sovits 驱动的状态说明（前端直接展示，不用自己拼文案）
_TTS_NOTES = {
    "local": "未启用服务端 TTS（DIGITAL_HUMAN_PROVIDER=local）：本轮不播报语音，只显示文字",
    "xmov": "魔珐星云自带 TTS：音色由魔珐控制台的应用配置与 XMOV_VOICE 决定",
}


@router.get("/tts/voices", response_model=TtsVoicesResponse)
def list_tts_voices() -> TtsVoicesResponse:
    """查询当前语音引擎与可选音色。

    音色来自 `GPT_SOVITS_VOICES_FILE`（默认 backend/data/tts_voices.json）。
    这里读的是**已构建好的 provider 快照**而非重新读文件：两者必须一致，
    否则界面会列出“选了却不生效”的音色（改完文件需重启后端，与 .env 一致）。
    """
    try:
        provider = get_digital_human_provider()
    except ValueError as exc:
        # 例：“xmov 却没填密钥”。设置面板在页面加载时就会请求本接口，
        # 不能让它 500 拖垮整个面板——把原因当成 note 返回。
        settings = get_settings()
        return TtsVoicesResponse(
            provider=(settings.digital_human_provider or "local").strip().lower(),
            server_tts=False,
            configured=False,
            note=f"数字人驱动不可用：{exc}",
        )

    if not isinstance(provider, GptSovitsDigitalHumanProvider):
        return TtsVoicesResponse(
            provider=provider.name,
            server_tts=provider.name == "xmov",
            configured=provider.name == "xmov",
            note=_TTS_NOTES.get(provider.name, ""),
        )

    config = provider.config
    voices = [
        TtsVoiceInfo(
            id=voice_id,
            label=ref.label or voice_id,
            is_default=bool(config.default_voice_id) and voice_id == config.default_voice_id,
        )
        for voice_id, ref in config.voices.items()
    ]
    configured = bool(config.ref_audio_path or config.voices)
    return TtsVoicesResponse(
        provider=provider.name,
        server_tts=True,
        configured=configured,
        default_voice=config.default_voice_id,
        voices=voices,
        emotion_voices=bool(config.emotion_voices),
        note="" if configured else "未配置参考音频（GPT_SOVITS_REF_AUDIO 或音色表）：将降级为无音频",
    )


# =============================================================
# 数字人凭证（魔珐 appId / appSecret）
# =============================================================
#
# 为什么存后端：三个界面的 origin 不同（控制台 / 桌宠窗是
# 127.0.0.1:34567，Web 端是 localhost:3000），localStorage 天然不共享，
# 而「用哪套密钥」必须是三处一致的事实。
#
# ⚠️ 这**不是「更安全」**：魔珐 SDK 是客户端渲染（`new XmovAvatar({appSecret})`），
# 密钥最终必然下发到浏览器。真正的防护是 CORS 只放行回环地址
# （见 app/config.py 的 cors_origin_regex）——**不要**把 CORS_ORIGINS 设成 "*"，
# 那等于把这份密钥发给任意网页。
#
# 两个形态各存一份（web = 横屏给 Web 端，pet = 竖屏给桌宠窗）：横竖屏是魔珐
# 控制台创建**应用**时定下的，容器比例必须与应用类型一致。

_AVATAR_CREDENTIAL_FORMS = ("web", "pet")

# 单字段长度上限：appId / appSecret 都是短字符串，给足余量又能挡住明显异常
_MAX_CREDENTIAL_LENGTH = 512


class AvatarCredentialsResponse(BaseModel):
    """某形态的凭证与来源（前端据此决定能不能起魔珐 SDK）。"""

    form: str = Field(description="形态：web（横屏，Web 端）| pet（竖屏，桌宠窗）")
    appId: str = Field(default="", description="魔珐 appId（未配置时是空串）")
    appSecret: str = Field(default="", description="魔珐 appSecret（未配置时是空串）")
    source: str = Field(description="来源：user（界面填写）| env（部署配置）| none（未配置）")
    configured: bool = Field(description="是否可用（等价于 source != 'none'）")


class AvatarCredentialsPatch(BaseModel):
    """凭证写入；两个字段都为空串 = 清除该形态的覆盖。"""

    form: str = Field(default="web", description="形态：web | pet")
    appId: str = Field(default="")
    appSecret: str = Field(default="")


def _credentials_path() -> Path:
    return Path(get_settings().avatar_credentials_path)


def _read_credentials() -> dict[str, dict[str, str]]:
    """读凭证文件；缺失 / 损坏 / 结构不对一律按「全部未设置」处理。

    与对话偏好同样的容错理由：一个被改坏的 JSON 不该让数字人设置面板起不来
    ——它只是「填过哪些密钥」，丢了顶多回到部署配置。
    """
    path = _credentials_path()
    if not path.is_file():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("数字人凭证文件读不动，按未配置处理：%s", path)
        return {}

    if not isinstance(raw, dict):
        return {}

    stored: dict[str, dict[str, str]] = {}
    for form in _AVATAR_CREDENTIAL_FORMS:
        entry = raw.get(form)
        if not isinstance(entry, dict):
            continue
        app_id = str(entry.get("appId") or "").strip()
        app_secret = str(entry.get("appSecret") or "").strip()
        # 半份凭证视为没有：两个都齐才算这一形态配好
        if app_id and app_secret:
            stored[form] = {"appId": app_id, "appSecret": app_secret}
    return stored


def _write_credentials(values: dict[str, dict[str, str]]) -> None:
    """原子写：先写临时文件再 rename，避免留下半截 JSON。"""
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _require_form(form: str) -> str:
    value = (form or "").strip().lower()
    if value not in _AVATAR_CREDENTIAL_FORMS:
        raise HTTPException(
            status_code=400,
            detail=f"未知形态：{form}（可选 {' / '.join(_AVATAR_CREDENTIAL_FORMS)}）",
        )
    return value


def _resolve_credentials(form: str) -> AvatarCredentialsResponse:
    """按「界面填写 > 部署配置」解析某形态的凭证。

    .env 的 XMOV_APP_ID / XMOV_SECRET 是**两个形态共用**的兜底：只建了一个应用
    的部署仍能跑起来（比例可能不对，但不至于不能用）。
    """
    stored = _read_credentials().get(form)
    if stored:
        return AvatarCredentialsResponse(
            form=form,
            appId=stored["appId"],
            appSecret=stored["appSecret"],
            source="user",
            configured=True,
        )

    settings = get_settings()
    app_id = (settings.xmov_app_id or "").strip()
    app_secret = (settings.xmov_secret or "").strip()
    if app_id and app_secret:
        return AvatarCredentialsResponse(
            form=form, appId=app_id, appSecret=app_secret, source="env", configured=True
        )

    return AvatarCredentialsResponse(form=form, source="none", configured=False)


@router.get("/avatar/credentials", response_model=AvatarCredentialsResponse)
def get_avatar_credentials(form: str = "web") -> AvatarCredentialsResponse:
    """查询某形态的魔珐凭证（界面填写优先，否则回落部署配置）。"""
    return _resolve_credentials(_require_form(form))


@router.put("/avatar/credentials", response_model=AvatarCredentialsResponse)
def put_avatar_credentials(payload: AvatarCredentialsPatch) -> AvatarCredentialsResponse:
    """写入 / 清除某形态的魔珐凭证（控制台 / Web 端 / 桌宠窗共用同一份）。"""
    form = _require_form(payload.form)
    app_id = payload.appId.strip()
    app_secret = payload.appSecret.strip()

    if len(app_id) > _MAX_CREDENTIAL_LENGTH or len(app_secret) > _MAX_CREDENTIAL_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"凭证过长（上限 {_MAX_CREDENTIAL_LENGTH} 字符）"
        )

    # 半份凭证必须拒掉，而且**不得落盘**：只写一半会让下一次读到
    # 「配了却用不了」的状态，用户还以为保存成功了
    if bool(app_id) != bool(app_secret):
        raise HTTPException(
            status_code=400, detail="appId 与 appSecret 必须同时填写或同时留空（清除）"
        )

    stored = _read_credentials()
    if app_id:
        stored[form] = {"appId": app_id, "appSecret": app_secret}
    else:
        stored.pop(form, None)
    _write_credentials(stored)

    return _resolve_credentials(form)


@router.get("/audio/{filename}")
def get_audio(filename: str) -> FileResponse:
    """读取已生成的音频文件（仅允许 media 目录内的音频后缀）。"""
    settings = get_settings()
    media_dir = Path(settings.media_dir).resolve()
    target = (media_dir / filename).resolve()

    # 安全校验：必须在 media 目录内，且为允许的音频后缀
    if not str(target).startswith(str(media_dir)):
        raise HTTPException(status_code=400, detail="非法路径")
    if target.suffix.lower() not in _ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="音频不存在")
    return FileResponse(target)
