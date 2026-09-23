"""tts 子包：文本 → 语音（音频字节）。

| 模块 | 职责 |
|---|---|
| `base.py` | `TtsProvider` 接口 + `TtsAudio`（通用输出）+ `NullTtsProvider` |
| `factory.py` | 按名创建实现（`create_tts_provider`） |
| `gpt_sovits_provider.py` | GPT-SoVITS 的 `TtsProvider` 实现（薄包装 `GptSovitsClient`） |
| `gpt_sovits.py` | GPT-SoVITS（官方 api_v2.py）客户端 |
| `voices.py` | 音色表（id → 参考音频）加载 |
| `audio.py` | 音频时长解析（零依赖，只用标准库） |

与 `app/digital_human/` 的分工：

- 本子包只负责**拿到音频**；
- `app/digital_human/` 负责把音频 + 情绪组装成**驱动时间轴**（口型 / 表情 / 动作）。

为什么这样分：魔珐星云自带 TTS（`app/digital_human/xmov_tts.py`，且能返回字级时间戳），
而 GPT-SoVITS 是**独立服务**、且不返回字级时间戳——两者的能力不同，
用一层薄接口隔离「怎么取音频」，驱动层就只需关心「拿到音频后怎么对齐口型」。

`AGENTS.md §9.10` 第 12 项：本能力已注册为 builtin 插件（`tts`），实现名
`gpt_sovits` / `none`，可被第三方替换。
"""

from app.tts.base import (
    DEFAULT_MEDIA_TYPE,
    NullTtsProvider,
    TtsAudio,
    TtsError,
    TtsNotAvailable,
    TtsProvider,
)
from app.tts.factory import (
    GPT_SOVITS_NAMES,
    TTS_PROVIDERS,
    create_tts_provider,
)

__all__ = [
    "DEFAULT_MEDIA_TYPE",
    "GPT_SOVITS_NAMES",
    "NullTtsProvider",
    "TTS_PROVIDERS",
    "TtsAudio",
    "TtsError",
    "TtsNotAvailable",
    "TtsProvider",
    "create_tts_provider",
]
