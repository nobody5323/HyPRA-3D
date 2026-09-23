"""core 能力清单：**只声明边界，不移动文件**（`AGENTS.md §9.10` / §9.11 P8）。

本模块是「哪些能力属于 `core`（换掉它产品就不成立，因此**不可禁用**）」的
**单一事实来源**。它只做聚合导出，不含任何实现——实现仍在各自的原有目录里：

| §9.10 的 9 项 core | 实际位置 |
| --- | --- |
| 1 `prompt-assembly` | `app/rag/prompt_manager.py`、`app/prompts/{renderer,sanitize}.py` |
| 2 `memory-facade` | `app/memory/store.py`、`app/memory/warm/decay.py` |
| 3 `conversation-graph` | `app/graph/{chat_graph,nodes,state}.py` |
| 4 `session-window` | `app/session/context.py` |
| 5 `worldbook-matcher` | `app/worldbook/{matcher,vector_index}.py` |
| 6 `emotion-pipeline` | `app/tools/emotion/` |
| 7 `embodiment-timeline` | `app/digital_human/ssml.py`（另含 `viseme.py` / `models.py`） |
| 8 `retrieval-fusion` | `app/rag/retrieval/hybrid.py` |
| 9 `api-skeleton` | `app/{main,config}.py` |

## 为什么不做物理移动（P8 的「轻」档）

core 边界在 P2（§9.11）之后**已经由各能力的 `base.py` + `factory.py` 固化了**——
`Tokenizer` / `DocumentParser` / `EmotionFallback` / `WarmMemoryStore` / `LLMProvider` /
`DigitalHumanProvider` / `KnowledgeStore` / `SessionStore`。「哪些是策略、哪些是可替换实现」
本来就有单一事实来源，只是它们分散在各模块里。

把 100+ 个文件移进 `app/core/` 与 `app/builtin/` 的收益，抵不上四类同步成本：
`AGENTS.md §3` 列出的构建配置引用、CI 路径、docs 引用、以及一次全量的 import 改写。
本模块让那件事**随时可做**（边界已经写死在这儿），但不必现在付那笔账。

## 用法

```python
from app.core import PromptManager, MemoryStore, reciprocal_rank_fusion
```

**注意**：`import app.core` 会**触发** `app.main` 的模块级 `app = create_app()`
（`api-skeleton` 的一部分）。这是刻意的——core 清单要覆盖应用工厂，就得接受这层副作用；
它同时是 API 测试与运行时的既定行为。
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.digital_human.ssml import (
    SpeakCommand,
    build_speak_command,
    build_ssml,
    resolve_ka_action,
)
from app.graph.chat_graph import build_chat_graph
from app.graph.nodes import ChatNodes
from app.graph.state import ChatState
from app.main import create_app
from app.memory.store import MemoryContext, MemoryStore
from app.memory.warm.decay import combined_score, time_decay_weight
from app.prompts.renderer import estimate_tokens
from app.prompts.sanitize import sanitize_reply
from app.rag.prompt_manager import BuiltPrompt, PromptManager
from app.rag.retrieval.hybrid import DEFAULT_RRF_K, reciprocal_rank_fusion
from app.session.context import ChatTurn, SessionContext, SessionSummary, build_session_title
from app.tools.emotion import (
    EmotionFallback,
    EmotionLabel,
    EmotionResult,
    build_emotion_tool,
    create_fallback,
    default_fallback,
    extract_emotion_fallback,
    parse_emotion_result,
)
from app.worldbook.matcher import match_entries
from app.worldbook.vector_index import WorldBookVectorIndex

#: 9 项 core 能力 → 它们对外的公开符号。
#:
#: 这张表既是文档，也是**可执行断言**：`tests/test_app/test_core_exports.py`
#: 会验证 ①每个符号真的存在 ②`__all__` 与并集一致 ③9 项一个不多一个不少。
#: 重构时若把某个 core 符号改名或挪走，测试会立刻失败——这正是本模块存在的意义。
#:
#: 导出原则：只列**接口与入口**（调用方会依赖的东西），不导出内部常量与实现细节。
#: 例如 `PromptManager` 的 `LAYER_*` 常量不在此列——它们是实现细节，
#: 需要的人自己去原模块取。
CORE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "prompt-assembly": ("BuiltPrompt", "PromptManager", "estimate_tokens", "sanitize_reply"),
    "memory-facade": ("MemoryContext", "MemoryStore", "combined_score", "time_decay_weight"),
    "conversation-graph": ("ChatNodes", "ChatState", "build_chat_graph"),
    "session-window": ("ChatTurn", "SessionContext", "SessionSummary", "build_session_title"),
    "worldbook-matcher": ("WorldBookVectorIndex", "match_entries"),
    "emotion-pipeline": (
        "EmotionFallback",
        "EmotionLabel",
        "EmotionResult",
        "build_emotion_tool",
        "create_fallback",
        "default_fallback",
        "extract_emotion_fallback",
        "parse_emotion_result",
    ),
    "embodiment-timeline": (
        "SpeakCommand",
        "build_speak_command",
        "build_ssml",
        "resolve_ka_action",
    ),
    "retrieval-fusion": ("DEFAULT_RRF_K", "reciprocal_rank_fusion"),
    "api-skeleton": ("Settings", "create_app", "get_settings"),
}

#: 导出名单**由能力表生成**，不手写：手写的列表迟早会与表脱节，
#: 而这两者不一致时 `from app.core import *` 会静默漏东西。
#: `tests/test_app/test_core_exports.py` 仍然就两者的一致性做断言（防以后有人改成静态列表又改错）。
__all__ = sorted(
    {
        "CORE_CAPABILITIES",
        *(name for symbols in CORE_CAPABILITIES.values() for name in symbols),
    }
)
