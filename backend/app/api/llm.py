"""对话模型的**运行时**切换（OpenAI 兼容 API）。

端点：

    GET    /llm/config        当前生效配置（**不含 api_key 明文**）
    PUT    /llm/config        切换模型：重建 provider + 失效已编译的图，下一轮即生效
    DELETE /llm/config        清除界面设置，回到 .env 的部署配置
    GET    /llm/models        拉取 {base_url}/models 的可用模型列表
    POST   /llm/config/test   连通性测试（发一条极短请求，**不改变**当前配置）

设计要点：

- **与 .env 分工**：`.env` 是部署配置（评审 / CI），运行时切换只写本地运行时文件；
  两者互不覆盖，重启后运行时设置仍在（除非显式 DELETE）。
- **与「模型预设档」分工**：这里选**用哪个模型**，预设档（`/chat/presets`）配的是
  **这个模型的采样参数**；切换模型后自动档会按新模型名重新匹配。

安全（红线）：`api_key` 只单向写入本地文件（`backend/data/`，已 gitignore），
**任何响应都不回传明文**（只回 `has_api_key`）；异常信息先脱敏再返回，也不写日志。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.chat import set_llm_provider
from app.config import get_settings
from app.llm.base import ChatMessage
from app.llm.factory import create_llm_provider
from app.llm.openai_compatible import DEFAULT_BASE_URLS
from app.llm.runtime import (
    PROVIDER_LABELS,
    PROVIDER_OPTIONS,
    LlmRuntimeConfig,
    clear_runtime_config,
    load_runtime_config,
    save_runtime_config,
)

router = APIRouter(prefix="/llm", tags=["llm"])

#: 进程内配置缓存：首次访问时从运行时文件（或 .env）装载
_runtime_config: LlmRuntimeConfig | None = None
_runtime_loaded = False


# --------------------------------------------------------------------------
# 配置读写
# --------------------------------------------------------------------------


def _env_config() -> LlmRuntimeConfig:
    """`.env` 里的部署配置（没有运行时配置时的回落）。"""
    settings = get_settings()
    return LlmRuntimeConfig(
        provider=settings.llm_provider or "mock",
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout,
        enable_thinking=settings.llm_enable_thinking,
    )


def get_llm_config() -> tuple[LlmRuntimeConfig, str]:
    """当前生效配置与来源（`runtime` = 界面设置 / `env` = 部署配置）。"""
    global _runtime_config, _runtime_loaded
    if not _runtime_loaded:
        _runtime_config = load_runtime_config()
        _runtime_loaded = True
    if _runtime_config is not None:
        return _runtime_config, "runtime"
    return _env_config(), "env"


def reset_llm_config_cache() -> None:
    """清空进程内缓存（测试注入用；外部改动运行时文件后也应调用）。

    注意与 DELETE 端点 `reset_llm_runtime_config` 区分：这个只清缓存，不删文件。
    """
    global _runtime_config, _runtime_loaded
    _runtime_config = None
    _runtime_loaded = False


def _apply_config(config: LlmRuntimeConfig, *, source: str) -> dict:
    """应用配置：重建 provider（`set_llm_provider` 会连带失效已编译的图）。"""
    global _runtime_config, _runtime_loaded

    error = config.validate()
    if error:
        raise HTTPException(status_code=400, detail=error)

    try:
        provider = create_llm_provider(**config.provider_kwargs())
    except Exception as exc:  # noqa: BLE001 - 配置问题属可预期的用户输入错误
        raise HTTPException(
            status_code=400, detail=f"提供商初始化失败：{_redact(str(exc), config.api_key)}"
        ) from exc

    _runtime_config = config
    _runtime_loaded = True
    set_llm_provider(provider)
    return config.to_public_dict(source=source)


def _redact(text: str, api_key: str) -> str:
    """把可能出现在异常信息里的 key 替换掉（绝不回传或记录用户凭证）。"""
    if not api_key:
        return text
    return text.replace(api_key, "***")


# --------------------------------------------------------------------------
# 请求模型
# --------------------------------------------------------------------------


class LlmConfigRequest(BaseModel):
    """切换对话模型。"""

    provider: str = Field(description="mock | dashscope | siliconflow | openai-compatible")
    base_url: str = Field(
        default="", description="端点地址；dashscope / siliconflow 留空即用默认值"
    )
    model: str = Field(default="", description="模型名，如 qwen2.5-7b-instruct")
    api_key: str | None = Field(
        default=None,
        description="新的 API Key；**留空 = 沿用已保存的**（前端拿不到明文，只提交要改的值）",
    )
    timeout: float | None = Field(default=None, description="单次请求超时（秒）")
    enable_thinking: bool | None = Field(
        default=None, description="推理模型思考开关：true 开启 / false 关闭 / null 不传"
    )
    persist: bool = Field(
        default=True, description="是否写入本地运行时配置（重启后保留；false = 只在本次运行生效）"
    )


class LlmTestRequest(BaseModel):
    """连通性测试（与切换请求同形，但不改变任何状态、不落盘）。"""

    provider: str = "openai-compatible"
    base_url: str = ""
    model: str = ""
    api_key: str | None = None
    timeout: float | None = None


# --------------------------------------------------------------------------
# 端点
# --------------------------------------------------------------------------


@router.get("/config")
def read_llm_config() -> dict:
    """当前生效的模型配置（**不含 api_key 明文**，只给 `has_api_key`）。"""
    config, source = get_llm_config()
    return {
        "config": config.to_public_dict(source=source),
        "providers": [{"value": name, **PROVIDER_LABELS[name]} for name in PROVIDER_OPTIONS],
        "default_base_urls": dict(DEFAULT_BASE_URLS),
    }


@router.put("/config")
def update_llm_config(req: LlmConfigRequest) -> dict:
    """切换对话模型（立即生效；默认写入本地运行时配置，重启后保留）。"""
    if req.provider not in PROVIDER_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"未知 provider：{req.provider!r}（可选：{'/'.join(PROVIDER_OPTIONS)}）",
        )

    current, _ = get_llm_config()
    submitted_key = req.api_key if req.api_key is not None else current.api_key
    config = LlmRuntimeConfig(
        provider=req.provider,
        base_url=(req.base_url or "").strip() or DEFAULT_BASE_URLS.get(req.provider, ""),
        model=(req.model or "").strip(),
        api_key=(submitted_key or "").strip(),
        timeout=req.timeout or current.timeout,
        enable_thinking=req.enable_thinking,
    )

    if req.persist:
        # 先写文件再应用：文件写入失败（磁盘只读等）时不要留下「内存已切换」的假象
        save_runtime_config(config)
    public = _apply_config(config, source="runtime" if req.persist else "session")
    return {"config": public}


@router.delete("/config")
def reset_llm_runtime_config() -> dict:
    """清除界面设置，回到 .env 的部署配置（同时删掉本地运行时文件）。"""
    removed = clear_runtime_config()
    reset_llm_config_cache()
    config, source = get_llm_config()
    _apply_config(config, source=source)
    return {"removed": removed, "config": config.to_public_dict(source=source)}


@router.get("/models")
def list_llm_models(
    base_url: str = Query(default="", description="端点地址；缺省用当前配置"),
    api_key: str | None = Query(
        default=None, description="临时 key（不传则用已保存的）；仅在本次请求内使用，不落盘"
    ),
    provider: str = Query(default="", description="provider 名；缺省用当前配置"),
) -> dict:
    """拉取可用模型列表（`GET {base_url}/models`）。

    用途：用户填好 key 与端点后**先看看有哪些模型**，避免手打模型名出错。
    失败时返回 400 与原始错误（已脱敏）——「拉不到列表」不该被当成服务器故障。
    """
    current, _ = get_llm_config()
    probe = LlmRuntimeConfig(
        provider=(provider or current.provider).strip() or current.provider,
        base_url=(base_url or current.base_url).strip() or current.base_url,
        model=current.model,
        api_key=(api_key if api_key is not None else current.api_key),
        timeout=min(current.timeout or 30.0, 30.0),
    )

    error = probe.validate()
    if error:
        raise HTTPException(status_code=400, detail=error)

    try:
        provider_obj = create_llm_provider(**probe.provider_kwargs())
        models = provider_obj.list_models()
    except Exception as exc:  # noqa: BLE001 - 失败原因要如实告诉用户
        raise HTTPException(
            status_code=400,
            detail=_redact(f"拉取模型列表失败：{exc}", probe.api_key)[:300],
        ) from exc

    return {"base_url": probe.base_url, "model": probe.model, "models": models}


@router.post("/config/test")
def test_llm_config(req: LlmTestRequest) -> dict:
    """连通性测试：发一条极短请求（max_tokens=1），返回成功与否与延迟。

    用于「应用」之前先确认 key / 端点 / 模型名三件套是否正确。
    **不改变当前配置、不落盘**；失败信息已脱敏。
    """
    current, _ = get_llm_config()
    probe = LlmRuntimeConfig(
        provider=(req.provider or current.provider).strip() or current.provider,
        base_url=(req.base_url or "").strip()
        or DEFAULT_BASE_URLS.get(req.provider, "")
        or current.base_url,
        model=(req.model or "").strip() or current.model,
        api_key=(req.api_key if req.api_key is not None else current.api_key),
        timeout=min(req.timeout or current.timeout or 30.0, 60.0),
        enable_thinking=None,
    )

    error = probe.validate()
    if error:
        return {"ok": False, "latency_ms": 0, "model": probe.model, "error": error}

    started = time.perf_counter()
    try:
        provider_obj = create_llm_provider(**probe.provider_kwargs())
        reply = provider_obj.chat(
            [ChatMessage(role="user", content="ping")], temperature=0.0, max_tokens=1
        )
    except Exception as exc:  # noqa: BLE001 - 测试端点把异常当**结果**返回，而非服务错误
        return {
            "ok": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": probe.model,
            "error": _redact(str(exc), probe.api_key)[:300],
        }

    return {
        "ok": True,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "model": probe.model,
        "provider": probe.provider,
        "reply_preview": (reply or "")[:40],
    }
