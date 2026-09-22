"""media 路由：数字人驱动与播报指令。

主路径（魔珐 SDK）：
    POST /media/speak     文本 + 情绪 → SSML 播报指令（前端 avatar.speak(ssml)）

扩展路径（自研/通用渲染，可接入任意 3D/2D 模型）：
    POST /media/avatar    文本 + 情绪 → 口型/表情/动作时间轴
    GET  /media/audio/{f} 读取已生成的音频文件（供前端播放）

设计：/media/* 为**同步**路由（FastAPI 放入线程池执行），
因此 provider 内部可用 asyncio.run 调用魔珐 WebSocket。
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.digital_human.base import DigitalHumanProvider
from app.digital_human.factory import create_digital_human_provider
from app.digital_human.ssml import build_speak_command, build_ssml, split_for_streaming

router = APIRouter(prefix="/media", tags=["media"])

# 进程内单例（测试可经 set_digital_human_provider 注入）
_provider: DigitalHumanProvider | None = None

# 允许的音频后缀（防目录穿越 + 限制类型）
_ALLOWED_AUDIO_SUFFIXES = {".pcm", ".mp3", ".wav", ".ogg", ".m4a"}


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
        )
    return _provider


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
        description="与 chunks 一一对应的 SSML 段（streaming=true 时）；逐段喂 SDK 播报",
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
    # 每段各自包一层 SSML：分段播报仍需走 SSML 通道——前端不应自行拼标签，
    # 否则会漏掉 XML 转义与 KA 事件结构。
    # KA 动作只放首段：一句话内连续触发多次动作指令会让数字人反复抖动，
    # 动作语义应当服务于整段表达。
    ssml_chunks = [
        build_ssml(chunk, command.ka_action if index == 0 else "")
        for index, chunk in enumerate(chunks)
    ]

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
    voice: str | None = Field(default=None, description="音色 ID（缺省用配置默认）")


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
