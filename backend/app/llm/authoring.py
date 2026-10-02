"""LLM 辅助创作：用当前对话模型生成**人设 / 技能**草稿（`AGENTS.md §9.12`）。

三条共同原则（两个使用者都必须遵守）：

1. **草稿先行**：本模块**只生成、不落盘**。落盘由各自的 writer 在用户确认后做
   （`app/skills/writer.py`；人设走既有的创作工坊接口）；
2. **只写用户目录**：生成物只可能进 `data/skills` 与 `data/studio`，
   永不改 `backend/` 下随包分发的第一方内容；
3. **写前校验、写后回读**：生成结果先过 pydantic 与宿主自己的解析器。

模型来源：**与对话共用同一套配置**（界面的「对话模型」或 `.env`），不新增模型开关——
否则「配了模型却生成不了」会变成第二个要排查的地方。

本地占位实现（mock）**直接拒绝**：它的回复是固定占位文本，生成结果必然不可用；
与其给用户一份没法用的草稿，不如明确告诉他先去配模型。

**插件不在本模块的范围内**：插件是「会被宿主 import 并执行」的代码，让模型现写现装
等于把「用户审阅」这一步挤掉。插件改为**由用户自己写、放进插件目录**（接口与示例见
`docs/plugin-development.md`），宿主只负责加载与权限强制。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeVar

from pydantic import BaseModel, Field, ValidationError

from app.llm.base import ChatMessage, LLMProvider
from app.paths import resource_path

logger = logging.getLogger(__name__)

#: 创作类提示词所在目录（只读资源，随包分发；新增文件必须同步 backend.spec 的 datas）
_PROMPT_DIR = ("app", "prompts", "authoring")

#: 生成时的采样参数：要的是稳定可用的结构化内容，不是文学创作
_TEMPERATURE = 0.6
_MAX_TOKENS = 2400

#: 按任务覆盖采样参数。人设是**创作型**任务：低温会让不同需求收敛成同一张脸
#: （用户的观感就是「死板单调、每次都差不多」），所以给更高的温度与更宽的正文预算；
#: 技能是方法论，保持低温求稳定可复现。未登记的任务用上面的默认值。
_SAMPLING_BY_TASK: dict[str, tuple[float, int]] = {
    "persona": (0.9, 3200),
}

#: id 规范（与插件 manifest 同口径：小写字母/数字/._-，≤64 字符）
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

#: 状态变量白名单（人设里可以用的宏）；与 app/prompts/state_vars/definitions.py 一致
_STATE_VARS = frozenset({"user_name", "char_name", "current_mood"})

_DraftT = TypeVar("_DraftT", bound=BaseModel)


class AuthoringUnavailable(RuntimeError):
    """当前没有可用于创作的模型（未配置 / 未就绪 / 是本地占位实现）。"""


class AuthoringFailed(RuntimeError):
    """模型给了回复，但没能解析成合格的结构（含重试后仍失败）。"""


# =============================================================
# 模型解析
# =============================================================

_provider_resolver: Callable[[], LLMProvider | None] | None = None


def set_provider_resolver(resolver: Callable[[], LLMProvider | None] | None) -> None:
    """替换模型解析器（测试注入用；None 表示恢复默认）。"""
    global _provider_resolver
    _provider_resolver = resolver


def _default_resolver() -> LLMProvider | None:
    """默认解析器：复用对话链路的 provider（进程内单例，已含降级与容错）。

    延迟导入 `app.api.chat`：那个模块反过来会 import 记忆 / 图等一大堆东西，
    放在模块级会让 `app.llm` 与上层形成环。
    """
    from app.api.chat import _safe_llm_provider  # noqa: PLC0415

    return _safe_llm_provider()


def resolve_authoring_provider() -> LLMProvider:
    """取可用于创作的模型；不可用时抛 `AuthoringUnavailable`（调用方映射成 400/503）。

    `mock` 被单独拦下：它是零依赖占位实现（离线演示链路用），回复是写死的文本，
    拿它生成人设 / 技能 / 插件只会得到一堆看起来像样、实际不能用的东西。
    """
    resolver = _provider_resolver or _default_resolver
    try:
        provider = resolver()
    except Exception as exc:  # noqa: BLE001 - 配置问题不抛原始栈，给可读原因
        raise AuthoringUnavailable(f"对话模型不可用：{exc}") from exc

    if provider is None:
        raise AuthoringUnavailable(
            "对话模型不可用（未配置或初始化失败）：请先在「对话模型」里配好 provider 与 API Key"
        )
    if (getattr(provider, "name", "") or "").strip().lower() == "mock":
        raise AuthoringUnavailable(
            "当前用的是本地占位模型（mock），它只会返回固定占位文本，无法生成可用内容。"
            "请先在「对话模型」里选择百炼 / 硅基流动 / 自定义兼容端点并填入 API Key。"
        )
    return provider


# =============================================================
# 提示词与 JSON 解析
# =============================================================


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    """读创作提示词（`app/prompts/authoring/<name>.md`）。

    提示词与代码分离的理由与 persona 预设一致：改措辞不该动代码，
    而这类文件的迭代频率远高于代码。带 lru_cache：一次进程只读一次盘。
    """
    path = resource_path(*_PROMPT_DIR, f"{name}.md")
    return path.read_text(encoding="utf-8")


def extract_json(text: str) -> Any:
    """从模型回复里抠出 JSON 对象。

    容错三件事（都是真实会遇到的）：去掉 ```json 代码围栏、忽略 JSON 前后的说明文字、
    取第一个括号配平的片段。取不到就抛 `ValueError`，由调用方决定要不要重试。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("模型回复为空")

    # ① 去掉代码围栏（```json ... ``` 或 ``` ... ```）
    fence = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    # ② 直接解析（最常见：模型规规矩矩只给 JSON）
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # ③ 找第一个括号配平的片段（忽略字符串内的括号与转义）
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("回复里没有 JSON 对象")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : index + 1])
    raise ValueError("JSON 对象没有闭合")


@dataclass
class DraftOutcome:
    """一次生成的结果：草稿 + 用的模型（界面据此显示「谁写的」）。"""

    draft: BaseModel
    model: str


def generate_draft(
    *,
    task: str,
    brief: str,
    model_cls: type[_DraftT],
    normalize: Callable[[_DraftT], _DraftT] | None = None,
    provider: LLMProvider | None = None,
) -> DraftOutcome:
    """生成一份结构化草稿：`task` 对应提示词文件名，`brief` 是用户的一句话需求。

    `normalize` 是草稿清洗器（如「变量必须在内置白名单内」「正文不能过短」）：
    它与 JSON 解析**共用同一套重试**——模型第一遍写错语法、正文写太短都是常事，
    拿具体错因再问一次往往就好了；两次都不行才报错，并把原始片段前 200 字带出去。
    """
    if not brief.strip():
        raise AuthoringFailed("请先用一句话描述你想要的内容")

    active = provider or resolve_authoring_provider()
    system = load_prompt(task)
    temperature, max_tokens = _SAMPLING_BY_TASK.get(task, (_TEMPERATURE, _MAX_TOKENS))
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=brief.strip()),
    ]

    raw = ""
    for attempt in range(2):
        # 模型调用本身也会失败（key 过期 / 断网 / 限流 / 欠费），而这是最常见的失败模式：
        # 不能让它变成裸 500——用户拿不到任何原因，只能看到「生成失败（500）」。
        # 口径与 `/llm/config/test` 一致：把异常当**结果**报回去（已脱敏）。
        try:
            raw = active.chat(messages, temperature=temperature, max_tokens=max_tokens) or ""
        except Exception as exc:  # noqa: BLE001 - 上游错误当作可展示的结果
            raise AuthoringFailed(f"模型调用失败：{_redact(str(exc), active)}"[:400]) from exc
        try:
            payload = extract_json(raw)
            if not isinstance(payload, dict):
                raise ValueError("顶层必须是 JSON 对象")
            draft = model_cls.model_validate(payload)
            if normalize is not None:
                draft = normalize(draft)
            return DraftOutcome(draft=draft, model=getattr(active, "model", "") or active.name)
        except (ValueError, ValidationError, AuthoringFailed) as exc:
            logger.info("创作草稿第 %d 次不通过：%s", attempt + 1, exc)
            messages.append(ChatMessage(role="assistant", content=raw[:4000]))
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        f"上面这份输出不能用（{exc}）。请**只**重新输出一个 JSON 对象，"
                        "不要任何解释文字、不要 markdown 代码围栏，字段与类型严格按前面的要求，"
                        "并修正上面指出的问题。"
                    ),
                )
            )

    raise AuthoringFailed(f"模型没有产出可用的结构化内容（原始回复开头：{raw[:200]!r}）")


# =============================================================
# 共用校验/清洗
# =============================================================


def _redact(text: str, provider: LLMProvider) -> str:
    """把可能混在异常信息里的密钥换成 `***`（不把凭证回传给前端，也不写进日志）。

    异常文本常带着请求头 / URL，所以这一步不是可选项；拿不到 key 时就只做截断。
    与 `app/api/llm.py::_redact` 同口径。
    """
    for attr in ("api_key", "_api_key"):
        key = getattr(provider, attr, "") or ""
        if isinstance(key, str) and key.strip():
            text = text.replace(key, "***")
    return text


def slugify_id(text: str, *, fallback: str = "generated") -> str:
    """把任意文本整理成合法 id（小写、连字符、长度受限）。

    模型给的 id 偶尔会带中文或空格——直接拒掉太苛刻，能救就救；
    救不出来（结果为空）才回落 `fallback`。
    """
    ascii_only = re.sub(r"[^a-zA-Z0-9._-]+", "-", text or "").strip("-._").lower()
    ascii_only = re.sub(r"-{2,}", "-", ascii_only)[:48].strip("-._")
    return ascii_only or fallback


def validate_id(value: str, *, label: str) -> str:
    """校验 id 合法性（非法时抛出带说明的 ValueError，由 API 映射成 400）。"""
    text = (value or "").strip()
    if not _ID_PATTERN.match(text):
        raise ValueError(f"{label} 需为小写字母/数字/点/下划线/连字符，且以字母或数字开头：{value!r}")
    return text


# =============================================================
# 三类草稿模型（与 app/prompts/authoring/*.md 的字段一一对应）
# =============================================================
#
# 字段与提示词里写给模型的 JSON 结构**必须同改**：改了一边另一边就会静默错位
# （模型少给一个字段只是 validation error，排查起来却是“怎么老是生成失败”）。


class PersonaDraft(BaseModel):
    """人设草稿（对应 `authoring/persona.md`）。"""

    name: str = Field(description="角色名")
    title: str = Field(default="", description="一句话定位")
    description: str = Field(default="", description="角色简介")
    tags: list[str] = Field(default_factory=list, description="标签")
    prompt: str = Field(description="人设正文")
    background: str = Field(default="", description="背景故事")
    variables: list[str] = Field(default_factory=list, description="用到的状态变量名")


class SkillDraft(BaseModel):
    """技能草稿（对应 `authoring/skill.md`）。"""

    id: str = Field(description="kebab-case 英文 id")
    name: str = Field(description="中文名称")
    description: str = Field(default="", description="一句话说它是什么")
    when_to_use: str = Field(default="", description="什么情况下该用")
    body: str = Field(description="Markdown 正文")


# =============================================================
# 草稿清洗（把模型的“差点意思”救回来，救不回来就明确报错）
# =============================================================


def normalize_persona_draft(draft: PersonaDraft) -> PersonaDraft:
    """清洗人设草稿：去空白、变量限白名单、必填项要有实质内容。"""
    name = draft.name.strip()
    prompt = draft.prompt.strip()
    if not name:
        raise AuthoringFailed("模型没有给出角色名，请再生成一次")
    if len(prompt) < 60:
        # 太短的“人设正文”实际上是模型偷懒：直接回报，让用户重试而不是默默存下一段废话
        raise AuthoringFailed(f"生成的人设正文过短（{len(prompt)} 字），请再生成一次")

    unknown = [item for item in draft.variables if item not in _STATE_VARS]
    if unknown:
        logger.info("人设草稿里出现未知状态变量，已剔除：%s", unknown)

    return PersonaDraft(
        name=name,
        title=draft.title.strip(),
        description=draft.description.strip(),
        tags=[item.strip() for item in draft.tags if item.strip()][:6],
        prompt=prompt,
        background=draft.background.strip(),
        variables=[item for item in draft.variables if item in _STATE_VARS],
    )


def normalize_skill_draft(draft: SkillDraft) -> SkillDraft:
    """清洗技能草稿：id 规范化 + 正文非空。"""
    body = draft.body.strip()
    if len(body) < 80:
        raise AuthoringFailed(f"生成的技能正文过短（{len(body)} 字），请再生成一次")
    return SkillDraft(
        id=validate_id(slugify_id(draft.id, fallback="ai-skill"), label="技能 id"),
        name=draft.name.strip() or "未命名技能",
        description=draft.description.strip(),
        when_to_use=draft.when_to_use.strip(),
        body=body,
    )
