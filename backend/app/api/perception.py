"""感知层接口：语音转写 + 桌面情景上报 + 状态查询。

设计见 `docs/proactive-multimodal.md` §4。三条与产品口径直接相关的约定：

1. **转写结果不自动发送**（§4.3）。返回文本给前端**填入输入框**，由用户确认后
   再走 `/chat`。ASR 有误识别，自动发送会让「识别错一个字」直接变成「对话跑偏」——
   让用户看一眼是零成本的保险。
2. **桌面情景只由客户端上报**，后端**不主动采集**。因此「关掉开关就真的没有采集」
   是结构上成立的，不是靠自觉。
3. **状态接口必须如实回 `available`**：拿不到能力时前端**不渲染麦克风**，
   而不是渲染一个点了会报错的按钮。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.config import get_settings
from app.perception.asr import AsrError, AsrProvider, create_asr_provider_from_settings
from app.perception.context import render_activity_lines, render_current, render_perception, summarize
from app.perception.models import DesktopContext, PerceptionEvent
from app.perception.profile import reset_profile_cache
from app.perception.snapshot import get_perception_snapshot
from app.perception.timeline import get_timeline, sync_activity_switch
from app.perception.vision import (
    VisionError,
    VisionProvider,
    create_vision_provider_from_settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/perception", tags=["perception"])

#: 影响「行踪是否该被记录」的配置项。
#:
#: 用途是给 `app/api/plugins.py` 的配置变更钩子做比对——用户在能力中心
#: 把行踪关掉的那一刻就要把已有记录清掉，而不是等桌宠端下一次上报
#: （它可能已经被关掉了）。清单放在这里而不是那里：感知层的配置依赖
#: 由感知层自己声明，别处只做比对（同 `EMBEDDING_CONFIG_FIELDS` 的口径）。
ACTIVITY_CONFIG_FIELDS: frozenset[str] = frozenset(
    {
        "perception_activity_enabled",
        "perception_activity_retention_days",
        "perception_activity_include_title",
        "perception_activity_idle_gap_seconds",
        "perception_activity_max_segment_hours",
    }
)

#: ASR provider 单例与它的配置签名。
#:
#: 为什么要按签名缓存：provider 会**加载模型**（几百 MB 到 1GB），
#: 每次请求重建 = 每次说话等十几秒。而配置可以在界面上改（§9.5 声明式配置），
#: 所以不能无脑缓存——用签名比对，配置真变了才重建。
_asr_provider: AsrProvider | None = None
_asr_signature: tuple = ()


def _asr_signature_of(settings) -> tuple:
    return (
        settings.asr_provider,
        settings.asr_model,
        settings.asr_model_dir,
        settings.asr_device,
        settings.asr_compute_type,
        settings.asr_beam_size,
    )


def get_asr_provider() -> AsrProvider:
    """取 ASR provider（配置未变则复用，避免重复加载模型）。"""
    global _asr_provider, _asr_signature
    settings = get_settings()
    signature = _asr_signature_of(settings)
    if _asr_provider is None or _asr_signature != signature:
        _asr_provider = create_asr_provider_from_settings(settings)
        _asr_signature = signature
    return _asr_provider


def set_asr_provider(provider: AsrProvider | None) -> None:
    """替换/重置 ASR provider（测试注入用）。

    **注入时必须把签名对齐当前配置**：否则下一次 `get_asr_provider()` 会认为
    「配置变了」（签名是空的）而按配置重建一个真实现，把注入的替身覆盖掉——
    表现是「测试里注入的假 provider 不生效」，而真实原因在签名比对上，极难定位。
    """
    global _asr_provider, _asr_signature
    _asr_provider = provider
    _asr_signature = _asr_signature_of(get_settings()) if provider is not None else ()


#: 视觉 provider 单例与配置签名（与 ASR 同一口径：配置真变了才重建）
_vision_provider: VisionProvider | None = None
_vision_signature: tuple = ()


def _vision_signature_of(settings) -> tuple:
    return (
        settings.vision_provider,
        settings.vision_model,
        settings.vision_base_url,
        settings.vision_api_key,
        settings.vision_timeout,
        settings.vision_prompt,
    )


def get_vision_provider() -> VisionProvider:
    """取视觉 provider（配置未变则复用）。"""
    global _vision_provider, _vision_signature
    settings = get_settings()
    signature = _vision_signature_of(settings)
    if _vision_provider is None or _vision_signature != signature:
        _vision_provider = create_vision_provider_from_settings(settings)
        _vision_signature = signature
    return _vision_provider


def set_vision_provider(provider: VisionProvider | None) -> None:
    """替换/重置视觉 provider（测试注入用）。

    签名对齐的理由同 `set_asr_provider`。
    """
    global _vision_provider, _vision_signature
    _vision_provider = provider
    _vision_signature = (
        _vision_signature_of(get_settings()) if provider is not None else ()
    )


# ---------- 语音识别 ----------


class AsrResponse(BaseModel):
    """一次转写的结果。"""

    text: str
    provider: str
    language: str = ""
    duration_seconds: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    #: 前端据此决定「要不要把文本放进输入框」——为空时不必覆盖用户已输入的内容
    has_text: bool = True


@router.post("/asr", response_model=AsrResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="录音（webm/ogg/wav/mp3 等）"),
    language: str = Query(default="", description="语言，留空用配置值"),
) -> AsrResponse:
    """把一段录音转写成文本。

    **只转写、不发送**：结果回给前端填进输入框，由用户确认后再走 `/chat`。
    """
    settings = get_settings()
    provider = get_asr_provider()
    if not provider.available():
        raise HTTPException(
            status_code=503,
            detail=(
                "语音识别不可用。请在「能力中心 → 语音识别」里选择 faster-whisper 并保存"
                "（需先在后端环境执行 pip install faster-whisper）。"
            ),
        )

    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=400, detail="音频为空。")

    limit_bytes = int(settings.asr_max_file_mb * 1024 * 1024)
    if len(audio) > limit_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"音频过大（{len(audio) / 1024 / 1024:.1f} MB，上限 "
                f"{settings.asr_max_file_mb:.0f} MB）。语音输入是「说一句话」，"
                "不是上传录音文件。"
            ),
        )

    try:
        # 转写是**同步 CPU 重活**（faster-whisper 内部是 C++）：直接调用会占住
        # asyncio 事件循环，把同一进程里的对话、SSE 推送、后台记忆写入全排到它后面。
        result = await run_in_threadpool(
            provider.transcribe,
            audio,
            language=(language or settings.asr_language),
            filename=file.filename or "audio.webm",
        )
    except AsrError as exc:
        # 400 而不是 500：模型缺失 / 依赖没装都是**配置问题**，
        # 详情原样展示给用户（里面带着「该把模型放到哪」）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 兜底，避免 500 无正文
        logger.warning("语音转写失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"语音转写失败：{exc}") from exc

    # 转写结果**不进记忆库**（§4.6）：它是感知，不是「关于这个人知道什么」。
    # 用户确认发送后，它才作为正常的用户输入走完整链路。
    return AsrResponse(
        text=result.text,
        provider=result.provider,
        language=result.language,
        duration_seconds=result.duration_seconds,
        warnings=result.warnings,
        has_text=bool(result.text.strip()),
    )


# ---------- 图片理解（Qwen2.5-VL）----------


class VisionResponse(BaseModel):
    """一次图片理解的结果。"""

    description: str
    provider: str
    model: str = ""
    warnings: list[str] = Field(default_factory=list)
    #: 后端当前渲染出的「此刻」文本（含这张图），便于前端展示与排查
    perception_text: str = ""


@router.post("/vision", response_model=VisionResponse)
async def describe_image(
    file: UploadFile = File(..., description="图片（png / jpg / webp）"),
    question: str = Query(default="", description="随图附带的问题（可选）"),
) -> VisionResponse:
    """理解用户分享的图片，结果作为**感知事实**注入。

    三条约定（§4.4）：

    1. **只由用户动作触发**。没有定时器、没有后台采集——结构上就不存在
       「关掉开关还在偷偷采」这种可能。
    2. **图片不落盘**。base64 内联进模型请求，后端不留副本。
    3. **结果只是事实，不是回复**。前端拿到描述后**仍然要发一条 `/chat`**
       （带一句「我发你一张图」之类的用户话），由人设决定怎么回应——
       这样「看图」与「说话」是两次调用、两个模型，视觉模型缺失时
       整个对话链路照常工作。
    """
    settings = get_settings()
    provider = get_vision_provider()
    if not provider.available():
        raise HTTPException(
            status_code=503,
            detail=(
                "图片理解不可用。请在「能力中心 → 图片理解」里选择 Qwen2.5-VL 并填入 API Key"
                "（或设置 VISION_PROVIDER / VISION_API_KEY）。"
            ),
        )

    image = await file.read()
    if not image:
        raise HTTPException(status_code=400, detail="图片内容为空。")

    limit_bytes = int(settings.vision_max_image_mb * 1024 * 1024)
    if len(image) > limit_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"图片过大（{len(image) / 1024 / 1024:.1f} MB，上限 "
                f"{settings.vision_max_image_mb:.0f} MB）。"
            ),
        )

    mime = (file.content_type or "image/png").split(";")[0].strip() or "image/png"
    try:
        # 调用是**阻塞的网络 IO**：放线程池，避免占住事件循环
        # （否则同一进程里的对话、SSE 推送都会被这一张图排到后面）
        result = await run_in_threadpool(
            provider.describe, image, mime=mime, question=question
        )
    except VisionError as exc:
        # 400 而不是 500：未配置 / key 无效都是**配置问题**，详情可原样展示
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 兜底，避免 500 无正文
        logger.warning("图片理解失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"图片理解失败：{exc}") from exc

    snapshot = get_perception_snapshot()
    perception_text = render_current(settings=settings)
    if not result.empty:
        snapshot.record(
            PerceptionEvent(
                source="vision",
                kind="user_image",
                payload={"description": result.description},
                ttl_seconds=settings.vision_ttl_seconds,
            )
        )
        perception_text = render_current(settings=settings)

    return VisionResponse(
        description=result.description,
        provider=result.provider,
        model=result.model,
        warnings=result.warnings,
        perception_text=perception_text,
    )


# ---------- 桌面情景 ----------


class DesktopReportResponse(BaseModel):
    """一次桌面情景上报的结果。"""

    accepted: bool
    enabled: bool
    perception_text: str = Field(
        default="", description="后端当前渲染出的「此刻」文本（调试用，便于对齐口径）"
    )


@router.post("/desktop", response_model=DesktopReportResponse)
def report_desktop(context: DesktopContext) -> DesktopReportResponse:
    """接收桌面情景上报（**只由 Electron 桌宠端调用**，Web 端没有这个能力）。

    进程边界（`docs/desktop-pet.md` §4）：主进程只负责**采集**，
    经 IPC 交给渲染层，再由渲染层直连本接口——主进程不直连后端、
    也不接触任何模型密钥。

    同一份上报顺带喂给**行踪**（`app/perception/timeline.py`）：
    轨迹只在「换了进程 / 段太长 / 人离开了」时才落盘，
    因此每 30 秒一次的调用不会产生写入放大。
    """
    settings = get_settings()
    timeline = get_timeline(settings=settings)

    if not settings.perception_desktop_enabled:
        # 开关关掉时**连快照都不写**：§4.6 的红线是「关掉开关后采集链路立即停止」，
        # 不是「采集了不用」。顺带清掉已有数据，避免旧事实继续参与提示词。
        get_perception_snapshot().clear(source="desktop")
        # 行踪同样要停：桌面情景是它**唯一**的数据来源，没有来源就没有行踪
        timeline.clear()
        return DesktopReportResponse(accepted=False, enabled=False)

    snapshot = get_perception_snapshot()
    snapshot.record_desktop(context)

    # 开关对账（幂等）：开着就记，关着就清。放在这里是为了覆盖「用户直接改了
    # .env 或运行时覆盖层、但没走能力中心那条钩子」的情形——两条路都能收敛。
    if settings.perception_activity_enabled:
        timeline.observe(context)
    else:
        sync_activity_switch(settings=settings)

    return DesktopReportResponse(
        accepted=True, enabled=True, perception_text=render_current(settings=settings)
    )


# ---------- 行踪（活动轨迹）----------


class ActivityClearResponse(BaseModel):
    """清除行踪的结果。"""

    removed: int
    message: str = ""


@router.post("/activity/clear", response_model=ActivityClearResponse)
def clear_activity() -> ActivityClearResponse:
    """清空全部行踪记录（界面「清除行踪记录」用）。

    这是 §4.6「感知数据不进记忆库、且用户全权可控」的落地动作：
    用户可以随时让它忘掉「最近在电脑上做过什么」，而且**一次清干净**——
    只清一半（比如留今天的）反而会让人怀疑「到底删了没」。
    """
    removed = get_timeline(settings=get_settings()).clear()
    # 画像缓存必须一起失效：它拿着清空前的聚合结果，
    # 不清的话界面上「最近的你」还会继续显示已经被删掉的行踪。
    reset_profile_cache()
    return ActivityClearResponse(
        removed=removed,
        message="已清除行踪记录。" if removed else "本来就没有行踪记录。",
    )


class ActivityResponse(BaseModel):
    """最近的行踪（给界面看，不是给模型的）。"""

    enabled: bool
    include_title: bool
    retention_days: int
    current: str = Field(default="", description="此刻在用什么（空 = 没采到）")
    lines: list[str] = Field(default_factory=list, description="最近几段，形如 `21:03–21:40 Code.exe`")
    profile_lines: list[str] = Field(default_factory=list, description="偏好画像渲染成的行")


@router.get("/activity", response_model=ActivityResponse)
def read_activity(hours: float = Query(default=12.0, gt=0, le=168)) -> ActivityResponse:
    """读最近的行踪与画像。

    **给界面用的**：让「它记住了什么」对用户可见（§4.6 的可观测性要求）。
    没有这个接口，用户只能靠「它说的话」去猜自己被记了什么——
    而猜出来的结论一定比实际更糟。
    """
    settings = get_settings()
    timeline = get_timeline(settings=settings)
    segments = timeline.segments(hours=hours) if settings.perception_activity_enabled else []
    # 同 `/status`：关掉时不报「此刻在用什么」（那是历史的读出口）
    current = timeline.current() if settings.perception_activity_enabled else None

    profile_lines: list[str] = []
    if settings.perception_profile_enabled and settings.perception_activity_enabled:
        # 画像依赖行踪（没有别的数据来源）——两处口径必须一致，
        # 否则会出现「行踪关了、界面还显示着基于行踪总结出来的印象」
        from app.perception.context import render_profile_facts  # noqa: PLC0415
        from app.perception.profile import get_profile  # noqa: PLC0415

        profile = get_profile(
            days=settings.perception_profile_days, settings=settings
        )
        if profile.usable:
            rendered = render_profile_facts(
                {
                    "days": profile.days,
                    "total_seconds": profile.total_seconds,
                    "top_processes": [
                        [name, seconds] for name, seconds in profile.top_processes
                    ],
                    "peak_window": list(profile.peak_window) if profile.peak_window else None,
                    "night_owl": profile.night_owl,
                }
            )
            profile_lines = rendered.splitlines() if rendered else []

    return ActivityResponse(
        enabled=settings.perception_activity_enabled,
        include_title=settings.perception_activity_include_title,
        retention_days=settings.perception_activity_retention_days,
        current=current.process if current is not None else "",
        lines=render_activity_lines(segments),
        profile_lines=profile_lines,
    )


# ---------- 状态 ----------


class PerceptionStatusResponse(BaseModel):
    """感知能力状态（前端据此决定渲染哪些入口）。"""

    desktop_enabled: bool
    include_window_title: bool
    asr_provider: str
    asr_available: bool
    asr_model: str
    asr_note: str = Field(default="", description="不可用时说明原因与怎么办")
    #: 是否在对话界面渲染语音按钮（关掉后能力仍在，只是不显示入口）
    asr_ui_enabled: bool = True
    #: 语音快捷键（形如 `Ctrl+Shift+M`；空 = 不启用）
    asr_shortcut: str = ""
    vision_provider: str
    vision_available: bool
    vision_model: str
    vision_note: str = Field(default="", description="不可用时说明原因与怎么办")
    #: 是否在对话界面渲染「图片」按钮
    vision_ui_enabled: bool = True
    #: 是否把时间（日期 / 星期 / 时段）告诉角色（§4.8）
    include_time: bool = True
    #: 行踪（活动轨迹）是否在记录（§4.9）
    activity_enabled: bool = True
    #: 行踪里是否连窗口标题一起记（默认 False）
    activity_include_title: bool = False
    #: 行踪保留天数
    activity_retention_days: int = 7
    #: 偏好画像是否注入
    profile_enabled: bool = True
    #: 此刻在用什么（空 = 没采到）
    activity_current: str = ""
    #: 最近几段行踪（给界面核对「它到底记了什么」）
    activity_lines: list[str] = Field(default_factory=list)
    current: list[str] = Field(default_factory=list, description="当前生效的感知事实（已渲染）")
    summary: str = ""


@router.get("/status", response_model=PerceptionStatusResponse)
def perception_status() -> PerceptionStatusResponse:
    """当前感知能力与状态。

    **不加载模型、不发起请求**：`available()` 只检查配置与依赖——
    状态查询是界面每次打开都会打的接口，在这里加载 1GB 模型或发一次
    探测请求，会让「打开设置页」变慢甚至变卡。
    """
    settings = get_settings()

    asr = get_asr_provider()
    asr_available = asr.available()
    asr_note = ""
    if not asr_available:
        asr_note = (
            "未启用或依赖未安装。选择 faster-whisper 后需在后端环境执行 "
            "pip install faster-whisper，并把模型权重放到 "
            f"{settings.asr_model_dir}/{settings.asr_model}/"
        )

    vision = get_vision_provider()
    vision_available = vision.available()
    vision_note = ""
    if not vision_available:
        vision_note = (
            "未启用或未配置。选择 Qwen2.5-VL 后需填入 API Key"
            "（阿里百炼 / 硅基流动的 OpenAI 兼容端点，无需新增依赖）。"
        )

    from app.perception.gather import current_events  # noqa: PLC0415

    events = current_events(settings=settings)
    rendered = render_perception(
        events, include_window_title=settings.perception_include_window_title
    )

    timeline = get_timeline(settings=settings)
    # 关掉行踪时**不报当前在用什么**：那是「历史」的读出口，
    # 开关关着还回一个程序名，界面会显示成「已关闭 · 此刻在用 Code.exe」。
    current_segment = timeline.current() if settings.perception_activity_enabled else None
    activity_lines = (
        render_activity_lines(timeline.segments(hours=settings.perception_activity_recent_hours))
        if settings.perception_activity_enabled
        else []
    )

    return PerceptionStatusResponse(
        desktop_enabled=settings.perception_desktop_enabled,
        include_window_title=settings.perception_include_window_title,
        asr_provider=asr.name,
        asr_available=asr_available,
        asr_model=settings.asr_model,
        asr_note=asr_note,
        asr_ui_enabled=settings.asr_ui_enabled,
        asr_shortcut=settings.asr_shortcut,
        vision_provider=vision.name,
        vision_available=vision_available,
        vision_model=settings.vision_model,
        vision_note=vision_note,
        vision_ui_enabled=settings.vision_ui_enabled,
        include_time=settings.perception_include_time,
        activity_enabled=settings.perception_activity_enabled,
        activity_include_title=settings.perception_activity_include_title,
        activity_retention_days=settings.perception_activity_retention_days,
        profile_enabled=settings.perception_profile_enabled,
        activity_current=current_segment.process if current_segment is not None else "",
        activity_lines=activity_lines,
        current=[line for line in rendered.splitlines() if line.strip()],
        summary=summarize(events),
    )
