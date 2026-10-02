"""创作工坊路由（/chat/studio）：用户自建角色卡、世界书条目与文风预设的编辑接口。

十六个端点：

    GET    /chat/studio/catalog                    一次性加载（角色 + 条目 + 文风 + 写作提示）
    GET    /chat/studio/personas/{id}              角色详情（含人设正文）
    POST   /chat/studio/personas                   新建角色
    PUT    /chat/studio/personas/{id}              更新角色
    DELETE /chat/studio/personas/{id}              删除角色（级联：会话 / 记忆 / 专属条目）
    POST   /chat/studio/personas/{id}/duplicate    复制角色（内置 → 我的）
    GET    /chat/studio/worldbook/{id}             条目详情
    POST   /chat/studio/worldbook                  新建条目
    PUT    /chat/studio/worldbook/{id}             更新条目
    DELETE /chat/studio/worldbook/{id}             删除条目
    PATCH  /chat/studio/worldbook/{id}/enabled     启用 / 停用
    POST   /chat/studio/worldbook/test             「试触发」（草稿不落盘）
    GET    /chat/studio/styles/{id}                文风详情（含风格指令与示例）
    POST   /chat/studio/styles                     新建文风
    PUT    /chat/studio/styles/{id}                更新文风
    DELETE /chat/studio/styles/{id}                删除文风（无级联）
    POST   /chat/studio/styles/{id}/duplicate      复制文风（内置 → 我的）

合规（AGENTS.md §6）：用户自撰内容属**本地数据**，只落 `STUDIO_DIR`
（默认 backend/data/studio，`backend/data/` 已被 .gitignore 覆盖），不进仓库、
不随发行物分发。内置角色、条目与文风不修改包内文件；用户可以直接删除它们，
删除结果写入用户侧隐藏清单。要编辑内置资源仍需先「复制」，
停用世界书则继续使用 `disabled_builtin.json`。

与对话链路的关系：本模块的**每个写操作**最后都会调用 `invalidate_chat_graph()`。
对话图在构造时会快照角色集合、世界书条目（后者还要一次性编码向量索引）与文风预设，
不失效就会出现「清单里看得到新角色 / 新文风、对话里却找不到」。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.chat import (
    get_embedding_provider,
    get_session_repository,
    get_studio_store,
    invalidate_chat_graph,
    purge_persona_memory,
)
from app.llm.authoring import (
    AuthoringFailed,
    AuthoringUnavailable,
    PersonaDraft,
    generate_draft,
    normalize_persona_draft,
)
from app.memory.warm.embedding import cosine_similarity
from app.prompts.persona.loader import PersonaPreset
from app.prompts.renderer import estimate_tokens
from app.prompts.state_vars.definitions import BUILTIN_STATE_VARS
from app.prompts.style.models import StylePreset
from app.studio import LIMITS, SAMPLING_SPEC, BuiltinReadOnlyError, StudioError
from app.worldbook.matcher import matched_keys, matched_regex
from app.worldbook.models import SCOPE_ALL, WorldBookEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/studio", tags=["studio"])

_T = TypeVar("_T")


def _run(action: Callable[[], _T]) -> _T:
    """把存储门面的异常映射成 HTTP 状态码（文案原样透出，用户才知道该怎么改）。

    顺序要紧：`BuiltinReadOnlyError` 是 `StudioError` 的子类，必须先匹配，
    否则内置只读会被当成普通参数错误返回 400（界面就没法区分「去复制一份」
    和「改一下参数」这两种下一步动作）。
    """
    try:
        return action()
    except BuiltinReadOnlyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _persona_payload(preset: PersonaPreset, builtin: bool) -> dict:
    """角色详情（**含人设正文**：这是用户自己的内容，界面要编辑它）。"""
    return {
        "id": preset.id,
        "name": preset.name,
        "title": preset.title,
        "description": preset.description,
        "tags": list(preset.tags),
        "background": preset.background,
        "prompt": preset.prompt,
        "variables": list(preset.variables),
        "creator": preset.creator,
        "builtin": builtin,
    }


def _entry_payload(entry: WorldBookEntry, builtin: bool) -> dict:
    return {**entry.model_dump(), "builtin": builtin}


def _style_payload(preset: StylePreset, builtin: bool) -> dict:
    """文风详情（**含风格指令与示例**：这是用户自己的内容，界面要编辑它）。"""
    return {
        "id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "tags": list(preset.tags),
        "style_prompt": preset.style_prompt,
        "avoid": list(preset.avoid),
        "examples": [
            {"user": example.user, "assistant": example.assistant}
            for example in preset.examples
        ],
        "sampling": dict(preset.sampling),
        "conflicts_with": list(preset.conflicts_with),
        "builtin": builtin,
    }


def _catalog() -> dict:
    """工坊的完整视图：角色 + 世界书 + 文风 + 写作提示。

    合成一个响应而不是拆成几个接口：界面每次打开/保存后都要同时刷新这几块，
    拆开只会变成几次请求，而这里的数据量只有几十 KB。
    """
    store = get_studio_store()
    personas, persona_warnings = store.list_personas()
    entries, entry_warnings = store.list_entries()
    styles, style_warnings = store.list_styles()
    return {
        "personas": [item.as_dict() for item in personas],
        "entries": [item.as_dict() for item in entries],
        "styles": [item.as_dict() for item in styles],
        "warnings": [*persona_warnings, *entry_warnings, *style_warnings],
        "limits": dict(LIMITS),
        "sampling_spec": [dict(item) for item in SAMPLING_SPEC],
        "scope_all": SCOPE_ALL,
        "state_vars": [
            {
                "name": var.name,
                "description": var.description,
                "default": var.default,
                "example": var.example,
            }
            for var in BUILTIN_STATE_VARS.values()
        ],
    }


# ---------- 请求模型 ----------


class PersonaCreateRequest(BaseModel):
    """新建角色卡。"""

    name: str = Field(description="角色名（显示用，可中文）")
    prompt: str = Field(description="人设正文（支持 {{user_name}} 等状态变量宏）")
    title: str = Field(default="", description="一句话角色定位（界面副标题）")
    description: str = Field(default="", description="角色简介")
    background: str = Field(
        default="", description="背景故事（常驻注入，排在人设正文之后）"
    )
    tags: list[str] = Field(default_factory=list, description="标签")
    variables: list[str] = Field(
        default_factory=list, description="声明用到的状态变量名（写作提示，供界面展示）"
    )


class PersonaUpdateRequest(BaseModel):
    """更新角色卡。

    取值语义与 ST 预设的覆盖层一致：**省略的字段不修改**（服务端用
    `exclude_unset` 取字段）。`null` 同样视为不修改；要清空请传空字符串
    （`name` / `prompt` 不接受清空）。
    """

    name: str | None = None
    prompt: str | None = None
    title: str | None = None
    description: str | None = None
    background: str | None = None
    tags: list[str] | None = None
    variables: list[str] | None = None


class PersonaDuplicateRequest(BaseModel):
    """复制角色。"""

    name: str | None = Field(
        default=None, description="副本的角色名；缺省为「原名（副本）」"
    )


class PersonaDraftRequest(BaseModel):
    """AI 生成人设草稿（`AGENTS.md §9.12`）。"""

    brief: str = Field(
        min_length=2,
        max_length=600,
        description="一句话需求，如「想做一个话少但很懂我的姐姐」",
    )


class EntryCreateRequest(BaseModel):
    """新建世界书条目。"""

    title: str = Field(description="条目标题（注入时作为块标题）")
    content: str = Field(description="触发后注入的正文")
    scope: str = Field(
        default=SCOPE_ALL, description="归属：`*` 对所有角色生效，或填角色 id"
    )
    keys: list[str] = Field(default_factory=list, description="关键词（任一命中即触发）")
    regex: list[str] = Field(default_factory=list, description="正则（任一命中即触发）")
    vector_text: str = Field(default="", description="语义触发文本（非空即启用向量通道）")
    vector_threshold: float = Field(
        default=0.7, ge=-1.0, le=1.0, description="语义通道命中阈值（需按 embedding 模型校准）"
    )
    case_sensitive: bool = Field(default=False, description="关键词是否区分大小写")
    priority: int = Field(default=0, description="注入优先级（越大越靠前）")
    enabled: bool = Field(default=True, description="是否参与触发")


class EntryUpdateRequest(BaseModel):
    """更新世界书条目（省略的字段不修改，语义同 `PersonaUpdateRequest`）。"""

    title: str | None = None
    content: str | None = None
    scope: str | None = None
    keys: list[str] | None = None
    regex: list[str] | None = None
    vector_text: str | None = None
    vector_threshold: float | None = Field(default=None, ge=-1.0, le=1.0)
    case_sensitive: bool | None = None
    priority: int | None = None
    enabled: bool | None = None


class EntryEnabledRequest(BaseModel):
    """启用 / 停用条目。"""

    enabled: bool = Field(description="true = 启用，false = 停用")


class EntryTestRequest(BaseModel):
    """「试触发」：用**草稿**（不落盘）判断一段样例文本是否命中。"""

    text: str = Field(description="用来试触发的样例文本（模拟用户会说的话）")
    companion_id: str = Field(
        default=SCOPE_ALL,
        description="按哪个陪伴对象试：填 `*` 模拟全局，填角色 id 模拟该角色的会话",
    )
    title: str = Field(default="", description="草稿标题（仅用于回显注入块）")
    content: str = Field(default="")
    keys: list[str] = Field(default_factory=list)
    regex: list[str] = Field(default_factory=list)
    vector_text: str = ""
    vector_threshold: float = Field(default=0.7, ge=-1.0, le=1.0)
    case_sensitive: bool = False


class StyleExampleInput(BaseModel):
    """一组示例对话（few-shot 示范目标语气）。"""

    user: str = Field(description="用户示例发言")
    assistant: str = Field(description="目标风格的助手回应")


class StyleCreateRequest(BaseModel):
    """新建文风预设。"""

    name: str = Field(description="文风名称（显示用，可中文）")
    style_prompt: str = Field(description="正向风格指令（放 system 末尾，影响更强）")
    description: str = Field(default="", description="一句话说明这种文风的特征")
    tags: list[str] = Field(default_factory=list, description="风格标签")
    avoid: list[str] = Field(
        default_factory=list, description="要避免的表达（克制使用，只列最刺眼的 AI 腔）"
    )
    examples: list[StyleExampleInput] = Field(
        default_factory=list, description="示例对话（2-3 组，few-shot 最有效）"
    )
    sampling: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "建议采样参数；只认 temperature / max_tokens / top_p / "
            "frequency_penalty / presence_penalty，超出范围或未知键会被拒绝"
        ),
    )
    conflicts_with: list[str] = Field(
        default_factory=list, description="与人设冲突的特质关键词（用于一致性告警，不阻断）"
    )


class StyleUpdateRequest(BaseModel):
    """更新文风预设（省略的字段不修改，语义同 `PersonaUpdateRequest`）。"""

    name: str | None = None
    style_prompt: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    avoid: list[str] | None = None
    examples: list[StyleExampleInput] | None = None
    sampling: dict[str, float] | None = None
    conflicts_with: list[str] | None = None


class StyleDuplicateRequest(BaseModel):
    """复制文风预设。"""

    name: str | None = Field(
        default=None, description="副本的名称；缺省为「原名（副本）」"
    )


# ---------- 读取 ----------


@router.get("/catalog")
def studio_catalog() -> dict:
    """一次性加载：角色清单 + 世界书条目 + 写作提示（变量名 / 字段上限）。"""
    return _catalog()


@router.get("/personas/{persona_id}")
def get_persona(persona_id: str) -> dict:
    """角色详情（含人设正文；内置角色也会返回，但界面须置为只读）。"""
    preset, builtin = _run(lambda: get_studio_store().get_persona(persona_id))
    return {"persona": _persona_payload(preset, builtin)}


@router.get("/worldbook/{entry_id}")
def get_entry(entry_id: str) -> dict:
    """条目详情。"""
    entry, builtin = _run(lambda: get_studio_store().get_entry(entry_id))
    return {"entry": _entry_payload(entry, builtin)}


# ---------- 角色：写入 ----------


@router.post("/personas/ai-draft")
def draft_persona(req: PersonaDraftRequest) -> dict:
    """用**当前对话模型**生成一份人设草稿（**不落盘**）。

    为什么单独做一个「草稿」端点而不直接建角色：人设是这个产品的核心内容，
    用户必须看到、改过、再决定保存（同 `ai-adapt` 的 `dry_run` 语义）。
    草稿由前端填进创作工坊的表单，落盘仍走 `POST /personas`——
    写路径只有一条，就少一种“写进去一半”的可能。

    模型不可用（未配置 / 本地 mock）返回 400，模型输出不可解析返回 502。
    """
    try:
        outcome = generate_draft(
            task="persona",
            brief=req.brief,
            model_cls=PersonaDraft,
            normalize=normalize_persona_draft,
        )
    except AuthoringUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AuthoringFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    logger.info("人设草稿已生成（模型 %s）", outcome.model)
    return {"draft": outcome.draft.model_dump(), "model": outcome.model}


@router.post("/personas")
def create_persona(req: PersonaCreateRequest) -> dict:
    """新建角色卡（id 由系统生成，创建后不可改——它是记忆隔离命名空间）。"""
    store = get_studio_store()
    preset = _run(
        lambda: store.create_persona(
            name=req.name,
            prompt=req.prompt,
            title=req.title,
            description=req.description,
            background=req.background,
            tags=req.tags,
            variables=req.variables,
        )
    )
    invalidate_chat_graph()
    return {"persona": _persona_payload(preset, False), "catalog": _catalog()}


@router.put("/personas/{persona_id}")
def update_persona(persona_id: str, req: PersonaUpdateRequest) -> dict:
    """更新我的角色卡（内置 → 403）。"""
    store = get_studio_store()
    preset = _run(
        lambda: store.update_persona(persona_id, **req.model_dump(exclude_unset=True))
    )
    invalidate_chat_graph()
    return {"persona": _persona_payload(preset, False), "catalog": _catalog()}


@router.delete("/personas/{persona_id}")
def delete_persona(persona_id: str) -> dict:
    """删除角色卡，并**级联清理**它的会话、记忆与专属世界书条目（**不可恢复**）。

    顺序：专属世界书条目 → 角色卡 → 该角色的全部会话 → 四层记忆
    （情景 / 事实 / 情绪日记 / 个人语料）。

    为什么级联：角色卡的价值在于「它带着一整套记忆」。id 创建后不可改（它是记忆
    隔离命名空间），删掉卡片后这批会话与记忆再没有第二个角色能认领——留着既访问
    不到、又占着库。专属条目同理：`scope` 指向已消失的角色，就成了永不触发的死条目。

    顺序有讲究：**先校验角色存在**，否则会先删掉专属条目、再在角色上抛 404，
    留下「条目没了、角色还在」的半截状态。

    清理不可逆，界面必须做二次确认；响应把各层实际删除的条数一并返回，
    用户才知道到底清掉了什么（失败层计 0，不谎报成功）。
    """
    store = get_studio_store()
    preset, _builtin = _run(lambda: store.get_persona(persona_id))

    removed_entries = _run(lambda: store.delete_bound_entries(persona_id))
    _run(lambda: store.delete_persona(persona_id))

    try:
        removed_sessions, removed_turns = get_session_repository().delete_sessions(
            persona_id
        )
    except Exception as exc:  # noqa: BLE001 - 一层失败不阻断其余层
        logger.warning("级联删除会话失败（%s）：%s", persona_id, exc)
        removed_sessions, removed_turns = 0, 0

    removed_memory = purge_persona_memory(persona_id)

    invalidate_chat_graph()
    logger.info(
        "删除角色：%s（会话 %d 段 / 消息 %d 条 / 专属条目 %d 条 / 记忆 %s）",
        persona_id,
        removed_sessions,
        removed_turns,
        len(removed_entries),
        removed_memory,
    )
    return {
        "deleted": preset.id,
        "name": preset.name,
        "removed_sessions": removed_sessions,
        "removed_turns": removed_turns,
        "removed_entries": len(removed_entries),
        "removed_memory": removed_memory,
        "catalog": _catalog(),
    }


@router.post("/personas/{persona_id}/duplicate")
def duplicate_persona(persona_id: str, req: PersonaDuplicateRequest) -> dict:
    """复制角色（内置角色借此得到一份可编辑的副本）。"""
    store = get_studio_store()
    copy = _run(lambda: store.duplicate_persona(persona_id, name=req.name))
    invalidate_chat_graph()
    return {"persona": _persona_payload(copy, False), "catalog": _catalog()}


# ---------- 世界书：写入 ----------


@router.post("/worldbook")
def create_entry(req: EntryCreateRequest) -> dict:
    """新建世界书条目（id 由系统生成）。"""
    store = get_studio_store()
    entry = _run(
        lambda: store.create_entry(
            title=req.title,
            content=req.content,
            keys=req.keys,
            regex=req.regex,
            vector_text=req.vector_text,
            vector_threshold=req.vector_threshold,
            enabled=req.enabled,
            priority=req.priority,
            scope=req.scope,
            case_sensitive=req.case_sensitive,
        )
    )
    invalidate_chat_graph()
    return {"entry": _entry_payload(entry, False), "catalog": _catalog()}


@router.put("/worldbook/{entry_id}")
def update_entry(entry_id: str, req: EntryUpdateRequest) -> dict:
    """更新我的条目（内置 → 403）。"""
    store = get_studio_store()
    entry = _run(
        lambda: store.update_entry(entry_id, **req.model_dump(exclude_unset=True))
    )
    invalidate_chat_graph()
    return {"entry": _entry_payload(entry, False), "catalog": _catalog()}


@router.delete("/worldbook/{entry_id}")
def delete_entry(entry_id: str) -> dict:
    """从当前工坊删除条目（内置资源写入用户侧隐藏清单）。"""
    store = get_studio_store()
    entry = _run(lambda: store.delete_entry(entry_id))
    invalidate_chat_graph()
    return {"deleted": entry.id, "title": entry.title, "catalog": _catalog()}


@router.patch("/worldbook/{entry_id}/enabled")
def set_entry_enabled(entry_id: str, req: EntryEnabledRequest) -> dict:
    """启用 / 停用条目。

    内置条目的开关只写**用户侧的停用偏好**（内置 YAML 一字未改）；
    我的条目直接改本体。
    """
    store = get_studio_store()
    _, builtin = _run(lambda: store.get_entry(entry_id))
    if builtin:
        _run(lambda: store.set_builtin_entry_enabled(entry_id, req.enabled))
    else:
        _run(lambda: store.update_entry(entry_id, enabled=req.enabled))
    invalidate_chat_graph()
    entry, _ = store.get_entry(entry_id)
    return {"entry": _entry_payload(entry, builtin), "catalog": _catalog()}


# ---------- 世界书：试触发 ----------


@router.post("/worldbook/test")
def test_entry(req: EntryTestRequest) -> dict:
    """「试触发」：用草稿条目判断一段样例文本是否命中（**不落盘**）。

    逐通道给出结论（命中了哪些关键词 / 哪个正则 / 语义相似度与阈值），
    因为只回一个 bool 的话，用户看到的永远是「没命中」却不知道为什么。

    草稿由 `StudioStore.build_draft_entry` 构造，清洗与校验规则与**写入路径
    完全一致**——这样「试的时候命中、存下来却不命中」（或反之）不可能发生。
    """
    store = get_studio_store()
    draft = _run(
        lambda: store.build_draft_entry(
            title=req.title,
            content=req.content,
            keys=req.keys,
            regex=req.regex,
            vector_text=req.vector_text,
            vector_threshold=req.vector_threshold,
            scope=req.companion_id,
            case_sensitive=req.case_sensitive,
        )
    )
    warnings: list[str] = []

    keys_hit = matched_keys(draft, req.text)
    regex_hit = matched_regex(draft, req.text)

    # 语义通道：与对话链路的 WorldBookVectorIndex 用同一个 embedding provider
    # （否则「试触发」和真实召回会因向量空间不同而结论不一致）
    vector_score: float | None = None
    if draft.vector_enabled:
        try:
            provider = get_embedding_provider()
            vector_score = cosine_similarity(
                provider.embed(req.text), provider.embed(draft.vector_text)
            )
        except Exception as exc:  # noqa: BLE001 - 语义算不出来不该让试触发失败
            warnings.append(f"语义通道计算失败（已跳过）：{exc}")

    vector_hit = vector_score is not None and vector_score >= draft.vector_threshold
    matched = bool(keys_hit or regex_hit or vector_hit)

    block = f"[{draft.title}]\n{draft.content}" if draft.content.strip() else ""
    return {
        "matched": matched,
        "keys_hit": keys_hit,
        "regex_hit": regex_hit,
        "vector_score": vector_score,
        "vector_threshold": draft.vector_threshold,
        "scope": draft.scope,
        "injected_text": block if matched else "",
        "estimated_tokens": estimate_tokens(block),
        "warnings": warnings,
    }


# ---------- 文风预设：读取与写入 ----------
#
# 文风与角色正交（人设管「是谁」、文风管「怎么说话」），所以这里没有归属
# 也没有级联：删除一份文风不会牵连任何会话或记忆，偏好里残留的 style_id
# 只会让那一轮回落默认档（`_resolve_style` 会给出告警）。
# 但**每个写操作仍然要 `invalidate_chat_graph()`**：对话图在构造时快照了文风集合，
# 不失效就会出现「工坊里建好了、对话里选不到」。


def _example_dicts(items: list[StyleExampleInput] | None) -> list[dict] | None:
    """请求模型 → 存储层接受的 dict 列表（None 保持 None = 不修改）。"""
    if items is None:
        return None
    return [item.model_dump() for item in items]


@router.get("/styles/{style_id}")
def get_style(style_id: str) -> dict:
    """文风详情（含风格指令与示例；内置文风也会返回，但界面须置为只读）。"""
    preset, builtin = _run(lambda: get_studio_store().get_style(style_id))
    return {"style": _style_payload(preset, builtin)}


@router.post("/styles")
def create_style(req: StyleCreateRequest) -> dict:
    """新建文风预设（id 由系统生成）。"""
    store = get_studio_store()
    preset = _run(
        lambda: store.create_style(
            name=req.name,
            style_prompt=req.style_prompt,
            description=req.description,
            tags=req.tags,
            avoid=req.avoid,
            examples=_example_dicts(req.examples),
            sampling=req.sampling,
            conflicts_with=req.conflicts_with,
        )
    )
    invalidate_chat_graph()
    return {"style": _style_payload(preset, False), "catalog": _catalog()}


@router.put("/styles/{style_id}")
def update_style(style_id: str, req: StyleUpdateRequest) -> dict:
    """更新我的文风预设（内置 → 403）。"""
    store = get_studio_store()
    # model_dump 已把嵌套的示例对话转成 dict，存储层的清洗/校验直接可用
    preset = _run(
        lambda: store.update_style(style_id, **req.model_dump(exclude_unset=True))
    )
    invalidate_chat_graph()
    return {"style": _style_payload(preset, False), "catalog": _catalog()}


@router.delete("/styles/{style_id}")
def delete_style(style_id: str) -> dict:
    """从当前工坊删除文风预设（内置资源写入用户侧隐藏清单，无级联）。"""
    store = get_studio_store()
    preset = _run(lambda: store.delete_style(style_id))
    invalidate_chat_graph()
    logger.info("删除文风预设：%s", preset.id)
    return {"deleted": preset.id, "name": preset.name, "catalog": _catalog()}


@router.post("/styles/{style_id}/duplicate")
def duplicate_style(style_id: str, req: StyleDuplicateRequest) -> dict:
    """复制文风预设（内置文风借此得到一份可编辑的副本）。"""
    store = get_studio_store()
    copy = _run(lambda: store.duplicate_style(style_id, name=req.name))
    invalidate_chat_graph()
    return {"style": _style_payload(copy, False), "catalog": _catalog()}
