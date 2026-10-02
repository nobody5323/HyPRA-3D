"""对话模型的**运行时**配置：可在界面上切换模型，无需改 .env 或重启后端。

为什么单独一层：

- `.env` 是**部署配置**（评审 / CI 用），不该被运行期操作改写；
- 运行期切换只影响本进程 + 本地运行时文件，两者语义互不干扰；
- 重启后运行时文件仍在 → 用户的切换得以保留（除非显式清除）。

存储位置：`backend/data/llm_runtime.json`（已被 .gitignore 的 `backend/data/` 覆盖）。

安全（红线）：

- `api_key` **只单向写入**：任何 API 响应都不回传明文（只回 `has_api_key`），
  也不写日志；文件落在本地数据目录，与导入的酒馆预设同一处理口径。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.paths import data_path


def default_runtime_path() -> Path:
    """运行时模型配置的默认落盘位置（可写数据目录）。"""
    return data_path("data", "llm_runtime.json")


#: 可选 provider（与 app/llm/factory.py 保持一致）
PROVIDER_OPTIONS = ("mock", "dashscope", "siliconflow", "openai-compatible")

#: 界面上的 provider 选项说明（前端直接渲染，避免两处硬编码不一致）
PROVIDER_LABELS: dict[str, dict[str, str]] = {
    "mock": {
        "label": "本地占位（mock）",
        "hint": "无需 key，回复为占位文本；用于离线演示与链路自检",
    },
    "dashscope": {
        "label": "阿里百炼（DashScope）",
        "hint": "默认端点 https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    "siliconflow": {
        "label": "硅基流动（SiliconFlow）",
        "hint": "默认端点 https://api.siliconflow.cn/v1",
    },
    "openai-compatible": {
        "label": "自定义 OpenAI 兼容端点",
        "hint": "任何 OpenAI 兼容服务（vLLM / Ollama / OneAPI / 官方 OpenAI…），需填 Base URL",
    },
}


@dataclass(frozen=True)
class LlmRuntimeConfig:
    """一次对话模型配置（不可变；改动用 `dataclasses.replace`）。"""

    provider: str = "mock"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout: float = 60.0
    enable_thinking: bool | None = None

    def to_public_dict(self, *, source: str = "runtime") -> dict:
        """给前端的配置快照——**绝不包含 api_key 明文**。"""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
            "enable_thinking": self.enable_thinking,
            "has_api_key": bool(self.api_key),
            "source": source,   # runtime（界面设置）| env（.env 默认）| session（本次运行，不落盘）
        }

    def provider_kwargs(self) -> dict:
        """传给 `create_llm_provider` 的关键字参数。"""
        return {
            "provider": self.provider,
            "api_key": self.api_key,
            "model": self.model,
            "base_url": self.base_url,
            "timeout": self.timeout,
            "enable_thinking": self.enable_thinking,
        }

    def validate(self) -> str | None:
        """返回错误说明；合法时返回 None（调用方据此回 400）。"""
        if self.provider not in PROVIDER_OPTIONS:
            return f"未知 provider：{self.provider!r}（可选：{'/'.join(PROVIDER_OPTIONS)}）"
        if self.provider == "mock":
            return None
        if not self.model.strip():
            return "缺少模型名（model）"
        if not self.api_key.strip():
            return "缺少 API Key"
        if self.provider == "openai-compatible" and not self.base_url.strip():
            return "自定义端点必须填写 Base URL"
        if self.timeout <= 0:
            return "超时时间必须为正数"
        return None


def runtime_config_path() -> Path:
    """运行时配置文件路径（可用环境变量 `LLM_RUNTIME_PATH` 覆盖，测试注入用）。"""
    override = os.environ.get("LLM_RUNTIME_PATH", "").strip()
    return Path(override) if override else default_runtime_path()


def load_runtime_config(path: str | Path | None = None) -> LlmRuntimeConfig | None:
    """读取运行时配置；文件不存在或损坏时返回 None（上层回落到 .env）。"""
    file_path = Path(path) if path is not None else runtime_config_path()
    if not file_path.is_file():
        return None
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    thinking = data.get("enable_thinking")
    timeout = data.get("timeout")
    try:
        resolved_timeout = float(timeout) if timeout is not None else 60.0
    except (TypeError, ValueError):
        resolved_timeout = 60.0

    return LlmRuntimeConfig(
        provider=str(data.get("provider") or "mock"),
        base_url=str(data.get("base_url") or ""),
        model=str(data.get("model") or ""),
        api_key=str(data.get("api_key") or ""),
        timeout=resolved_timeout,
        enable_thinking=thinking if isinstance(thinking, bool) else None,
    )


def save_runtime_config(config: LlmRuntimeConfig, path: str | Path | None = None) -> Path:
    """原子写入运行时配置（**含 api_key**，只落本地数据目录，永不回传/记录）。"""
    file_path = Path(path) if path is not None else runtime_config_path()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_name(file_path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, file_path)
    return file_path


def clear_runtime_config(path: str | Path | None = None) -> bool:
    """删除运行时配置（回到 .env 默认）。返回是否真的删除了文件。"""
    file_path = Path(path) if path is not None else runtime_config_path()
    if file_path.is_file():
        file_path.unlink()
        return True
    return False


def merge_api_key(config: LlmRuntimeConfig, api_key: str | None) -> LlmRuntimeConfig:
    """key 的合并规则：`None` = 沿用旧值。

    前端拿不到明文（只在本地文件里），所以提交时只能给「要改的新值」或留空；
    留空视为沿用，避免用户每改一次模型名就要重填 key。
    """
    if api_key is None:
        return config
    return replace(config, api_key=api_key.strip())
