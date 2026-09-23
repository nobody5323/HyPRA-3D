"""内置插件注册：把既有扩展点收编进插件体系。

本模块是**唯一的**「业务代码 → 插件体系」粘合层（`AGENTS.md §9.1`）：其余模块不需要
知道插件体系的存在，因此这一步是**零重构**的——只做包装，不改任何既有实现。

被收编的 13 项（对应 §9.1 盘点表 + §9.11 P2）：

    llm-providers       → LLM provider 工厂（mock / dashscope / siliconflow / openai-compatible）
    avatar-providers    → 数字人驱动工厂（local / xmov / gpt_sovits）
    tts                 → 语音合成（gpt_sovits / none）—— §9.10 第 12 项：从 digital_human 分出
    embedding           → 文本向量化（deterministic / dashscope / siliconflow / openai-compatible）
    memory-warm         → 情景记忆存储（inmemory / qdrant）
    memory-knowledge    → 个人记忆存储（inmemory / qdrant）
    session-store       → 会话持久化（memory / sqlite）
    tools-builtin       → 4 个情感陪伴工具（情绪日记 / 趋势 / 呼吸 / 记忆检索）
    mcp-bridge          → MCP Client（外部工具接入）
    tokenizer           → 中文分词（jieba / char-bigram）—— P2 从模块级函数拆为可替换策略
    knowledge-parser    → 文档解析（text / md / pdf / docx）—— P2 同上
    knowledge-chunker   → 文档分块（semantic / plain）—— P2 同上
    preset-ai-adaptation → 预设适配动作（detect / plan / build_diff；按**动作名**寻址）

统一约定：provider 工厂签名为 `(name: str, **kwargs) -> 实现`，`name` 是**实现名**
（如 `"qdrant"`），由调用方透传；插件负责适配既有工厂的各自签名。

注：情绪兜底策略（§9.11 P2 的第三项）**刻意不收编到这里**——它属于 core 的
`emotion-pipeline`（关掉它情绪链路就不成立，见 §9.2），只在 core 内部抽了接口，
由 `EMOTION_FALLBACK` 配置选择实现。放进 builtin 会让它变成可禁用，语义不符。
"""

from __future__ import annotations

from typing import Any

from app.plugins.capabilities import CapabilityType, Permission, PluginLayer
from app.plugins.manifest import PluginManifest
from app.plugins.registry import PluginRegistration, PluginRegistry

#: 内置插件统一版本（与后端包版本对齐；后续由 CI 校验一致性）
_BUILTIN_VERSION = "0.1.0"


def _builtin_manifest(
    plugin_id: str,
    display_name: str,
    *,
    category: str,
    description: str,
    capabilities: list[CapabilityType],
    loading_order: int = 100,
) -> PluginManifest:
    """构造内置插件的 manifest（无文件权限：内置实现只经宿主内存与配置工作）。"""
    return PluginManifest(
        id=plugin_id,
        display_name=display_name,
        version=_BUILTIN_VERSION,
        layer=PluginLayer.BUILTIN,
        category=category,
        description=description,
        capabilities=capabilities,
        loading_order=loading_order,
        permissions=Permission(),
    )


# =============================================================
# 适配器：把既有工厂各自不同的签名统一为 (name, **kwargs) -> 实现
# 用函数内 import：避免插件框架在导入期反向依赖业务模块，也加快宿主启动。
# =============================================================


def _llm_provider(name: str, **kwargs: Any) -> Any:
    from app.llm.factory import create_llm_provider

    return create_llm_provider(name or "mock", **kwargs)


def _digital_human_provider(name: str, **kwargs: Any) -> Any:
    from app.digital_human.factory import create_digital_human_provider

    return create_digital_human_provider(name or "local", **kwargs)


def _tts_provider(name: str, **kwargs: Any) -> Any:
    """按实现名取语音合成；空则 none（不做合成，前端回落浏览器 TTS）。"""
    from app.tts.factory import create_tts_provider

    return create_tts_provider(name, **kwargs)


def _warm_store(name: str, **kwargs: Any) -> Any:
    from app.memory.warm.factory import create_warm_store

    return create_warm_store(name or "memory", **kwargs)


def _embedding_provider(name: str, **kwargs: Any) -> Any:
    """按实现名取文本向量化 provider；空则用零依赖的确定性实现。

    注意既有工厂的首个参数叫 `provider` 而不是 `name`——适配器的职责之一就是
    抹平这类命名差异（与 `_session_store` 抹平 `db_path` 位置参数同理）。
    """
    from app.memory.warm.embedding import create_embedding_provider

    return create_embedding_provider(name, **kwargs)


def _knowledge_store(name: str, **kwargs: Any) -> Any:
    from app.memory.knowledge.factory import create_knowledge_store

    return create_knowledge_store(name or "memory", **kwargs)


def _session_store(name: str, **kwargs: Any) -> Any:
    from app.session.factory import create_session_store

    # 既有签名的 db_path 是位置参数（与其它工厂不同），在此对齐
    return create_session_store(name or "memory", kwargs.get("db_path", ""))


def _mcp_manager(name: str, **kwargs: Any) -> Any:  # noqa: ARG001 - 保留统一签名
    from app.mcp.manager import McpManager

    return McpManager(**kwargs)


def _builtin_tools() -> list[Any]:
    """既有内置工具作为 `ToolSpec` 列表（供插件注册表聚合）。"""
    from app.tools.builtin_tools import build_default_registry

    registry = build_default_registry()
    return [registry.get(name) for name in registry.names()]


def _tokenizer(name: str, **kwargs: Any) -> Any:  # noqa: ARG001 - 保留统一签名
    """按实现名取分词器；空则走 auto（有 jieba 用 jieba，否则降级 bigram）。"""
    from app.rag.retrieval.tokenize.factory import create_tokenizer

    return create_tokenizer(name or "auto")


def _parser(name: str, **kwargs: Any) -> Any:  # noqa: ARG001 - 保留统一签名
    """按**格式名**取文档解析器；name 为空时返回按文件名自动选的 `parse` 入口。

    parser 与其余 provider 不同：它不「按名创建」而是**按上传文件的扩展名**选，
    所以这里把能力面开成两层——`provider("parser", "pdf")` 取具体实现，
    `provider("parser")` 取自动选择的入口。
    """
    from app.memory.knowledge.parser import factory

    if not name:
        return factory.parse
    for parser in factory.PARSERS:
        if parser.name == name:
            return parser
    known = " | ".join(parser.name for parser in factory.PARSERS)
    raise ValueError(f"未知文档格式：{name!r}（可选 {known}）")


def _chunker(name: str, **kwargs: Any) -> Any:
    """按实现名取分块器；空则 auto（给了 embed 用语义边界，否则纯递归）。"""
    from app.memory.knowledge.chunker.factory import create_chunker

    return create_chunker(name, **kwargs)


def _preset_adaptation(name: str, **kwargs: Any) -> Any:  # noqa: ARG001 - 保留统一签名
    """按**动作名**取预设适配能力。

    与其他 provider 不同，这里的 `name` 不是「实现名」而是「动作名」——
    预设适配天然是**一组动作**（体检 / 规划 / 差异 / 载规则），而不是若干可换的实现；
    硬造一个只有一个实现的策略接口，只是把“包装”写成“架构”。
    空名返回整组，便于调用方一次取走。
    """
    from app.prompts import adaptation

    actions: dict[str, Any] = {
        "detect": adaptation.detect,
        "plan": adaptation.plan,
        "build_diff": adaptation.build_diff,
        "load_rules": adaptation.load_rules,
        "load_rules_file": adaptation.load_rules_file,
    }
    key = (name or "").strip()
    if not key:
        return actions
    if key not in actions:
        raise ValueError(f"未知适配动作：{name!r}（可选 {' | '.join(actions)}）")
    return actions[key]


# =============================================================
# 注册入口
# =============================================================


def build_builtin_registrations() -> list[PluginRegistration]:
    """构造全部内置插件的注册对象（不写入注册表，便于测试检查）。"""
    return [
        PluginRegistration(
            manifest=_builtin_manifest(
                "llm-providers",
                "对话模型接入",
                category="provider",
                description=(
                    "LLM provider 工厂：mock（零依赖占位）/ dashscope（阿里百炼）"
                    "/ siliconflow（硅基流动）/ openai-compatible（任意兼容端点）"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=100,
            ),
            providers={"llm": _llm_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "avatar-providers",
                "数字人驱动",
                category="provider",
                description=(
                    "具身驱动 provider 工厂：local（零依赖降级）/ xmov（魔珐星云）"
                    "/ gpt_sovits（自部署 TTS）"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=110,
            ),
            providers={"digital_human": _digital_human_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "tts",
                "语音合成",
                category="media",
                description=(
                    "文本转语音：gpt_sovits（自部署服务）/ none（不做合成，前端回落浏览器 TTS）；"
                    "音频交给数字人驱动组装口型时间轴"
                ),
                capabilities=[CapabilityType.PROVIDER],
                # 紧跟 avatar-providers：数字人驱动会经它取音频
                loading_order=112,
            ),
            providers={"tts": _tts_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "embedding",
                "文本向量化",
                category="memory",
                description=(
                    "把文本编码为向量：deterministic（零依赖本地，默认）/ dashscope "
                    "/ siliconflow / openai-compatible；温层召回与知识库共用同一实现"
                ),
                capabilities=[CapabilityType.PROVIDER],
                # 排在 memory-warm（120）之前：温层与知识库的向量都靠它
                loading_order=115,
            ),
            providers={"embedding": _embedding_provider},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "memory-warm",
                "情景记忆存储",
                category="memory",
                description="温层向量存储：memory（零依赖内存）/ qdrant（本地嵌入式或云端）",
                capabilities=[CapabilityType.PROVIDER],
                loading_order=120,
            ),
            providers={"warm_store": _warm_store},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "memory-knowledge",
                "个人记忆存储",
                category="memory",
                description="知识库（用户上传语料）存储：memory / qdrant",
                capabilities=[CapabilityType.PROVIDER],
                loading_order=130,
            ),
            providers={"knowledge_store": _knowledge_store},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "session-store",
                "会话持久化",
                category="memory",
                description="会话与滚动窗口的存储后端：memory（进程内）/ sqlite（落库）",
                capabilities=[CapabilityType.PROVIDER],
                loading_order=140,
            ),
            providers={"session_store": _session_store},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "tools-builtin",
                "内置陪伴工具",
                category="tool",
                description=(
                    "4 个情感陪伴工具：情绪日记 / 情绪趋势 / 呼吸练习 / 记忆检索"
                    "（模型通过 function calling 自主调用）"
                ),
                capabilities=[CapabilityType.TOOL],
                loading_order=150,
            ),
            tools=_builtin_tools(),
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "mcp-bridge",
                "MCP 外部工具接入",
                category="integration",
                description="MCP Client：连接外部 MCP server，把工具接进 Agent 行动层",
                capabilities=[CapabilityType.TOOL],
                loading_order=160,
            ),
            providers={"mcp_manager": _mcp_manager},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "tokenizer",
                "中文分词",
                category="retrieval",
                description=(
                    "BM25 稀疏检索的分词器：jieba（主）/ char-bigram（零依赖降级）；"
                    "由 TOKENIZER_BACKEND 选择"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=170,
            ),
            providers={"tokenizer": _tokenizer},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "knowledge-parser",
                "文档解析",
                category="retrieval",
                description=(
                    "个人记忆的上传解析：text / md / pdf / docx；"
                    "pdf 与 docx 的可选依赖惰性导入，缺失只影响该格式"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=180,
            ),
            providers={"parser": _parser},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "knowledge-chunker",
                "文档分块",
                category="retrieval",
                description=(
                    "把长文本切成入库与召回的分块：semantic（语义边界）/ plain（纯递归，可复现）；"
                    "长度硬约束与 overlap 由共用骨架保证"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=190,
            ),
            providers={"chunker": _chunker},
        ),
        PluginRegistration(
            manifest=_builtin_manifest(
                "preset-ai-adaptation",
                "预设 AI 适配",
                category="prompt",
                description=(
                    "导入酒馆预设后的一键适配：规则层确定性体检与修复（detect / build_diff）"
                    "+ 规划层攒成一次模型改写（plan）"
                ),
                capabilities=[CapabilityType.PROVIDER],
                loading_order=195,
            ),
            providers={"preset_adaptation": _preset_adaptation},
        ),
    ]


def register_all_builtin(registry: PluginRegistry) -> list[str]:
    """把内置插件注册进注册表；返回注册的插件 id 列表。"""
    registrations = build_builtin_registrations()
    for registration in registrations:
        registry.register(registration)
    return [r.manifest.id for r in registrations]
