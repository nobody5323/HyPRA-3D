"""数据源 → persona 的宿主侧映射（`AGENTS.md §9.4`）。

酒馆角色卡 → `PersonaPreset`：只做「字段搬家 + 宏对齐」，不改写内容。

四条映射约定：

1. **宏对齐**：酒馆卡正文里的 `{{char}}` / `{{user}}` 换成本项目的
   `{{char_name}}` / `{{user_name}}`。不对齐的话它们会以字面量进 prompt，
   还会让宏解析器逐条告警（`app/prompts/state_vars/resolver.py` 对未知变量保留原文）。
2. **长度上限与自建角色一致**（`app/studio/store.py` 的 `_MAX_PERSONA_*`）：
   persona 层是 `mandatory=True`、**不受预算裁剪**（`app/rag/prompt_manager.py`），
   超长会直接吃掉整个上下文——必须在这里截断并告知用户。
3. **不可直接编辑**：映射结果以「内置角色」身份并入清单，工作台里不能改正文，
   但可以「复制到我的」变成可编辑副本，也可以从当前工坊删除（写用户侧隐藏清单）。
4. **归属用得到的 id 是同一个**：`persona_id_for()` 既给 persona 用，也给
   角色内嵌世界书定 `scope` 用（见 `app/worldbook/datasource_map.py`），
   这样「这个角色的专属设定只在这个角色下注入」才成立。
"""

from __future__ import annotations

import re

from app.plugins.contracts import DataSourceCharacter, DataSourceSnapshot
from app.plugins.datasources import read_snapshots, stable_item_id
from app.prompts.persona.loader import PersonaPreset

#: persona 的宿主 id 命名空间（与世界书条目的 `tb-` 分开，避免两个命名空间相撞）
PERSONA_ID_PREFIX = "tbp"

#: 与「我的角色」一致的字段上限（超出即截断并告警；见模块文档第 2 条）
_MAX_NAME = 50
_MAX_TITLE = 100
_MAX_DESCRIPTION = 500
_MAX_PROMPT = 8000
_MAX_BACKGROUND = 8000
_MAX_TAG_ITEMS = 20
_MAX_TAG_CHARS = 30

#: 酒馆宏 → 本项目状态变量（数据格式兼容，不改写则字面量进 prompt）
_ST_MACROS: dict[str, str] = {"char": "char_name", "user": "user_name"}
_MACRO_PATTERN = re.compile(r"\{\{\s*(char|user)\s*\}\}", re.IGNORECASE)

#: 角色名没出现在正文里时补的身份句。用宏而不是字面名字，
#: 这样与内置人设（「你是苏澄，……」）同一套写法，也不会写死名字。
_IDENTITY_LINE = "你是{{char_name}}。"

#: 角色卡里本项目暂不使用的字段（读到了但用不上，必须让用户知道）
_UNUSED_FIELDS = (
    ("first_message", "开场白"),
    ("alternate_greetings", "备选开场白"),
    ("example_messages", "示例对话"),
)


def persona_id_for(character: DataSourceCharacter) -> str:
    """角色卡对应的宿主 persona id（同时也是它的记忆作用域键）。"""
    return stable_item_id(PERSONA_ID_PREFIX, character.source, character.source_id)


def scopes_by_character_name(snapshot: DataSourceSnapshot) -> dict[str, str]:
    """角色名 → persona id（同一份快照内的归属表）。

    **两个消费者共用同一个 id 函数**：世界书里角色内嵌书的 `scope`，以及酒馆会话
    该写进哪个陪伴对象的记忆。两处各算各的话，条目或记忆就会挂在一个永远匹配不上的
    id 上——那种 bug 表现为「明明导了却召不回」，很难查。

    插件把归属写成角色**显示名**（会话的 `character_name`、内嵌书的
    `extra["character"]` 是同一个名字），宿主只能按名字对上。
    同名角色卡（罕见）取第一张：宁可少注入，也不能把两张卡的内容搞混。
    """
    scopes: dict[str, str] = {}
    for character in snapshot.characters:
        scopes.setdefault(character.name, persona_id_for(character))
    return scopes


def align_macros(text: str) -> str:
    """把酒馆的 `{{char}}` / `{{user}}` 换成本项目的 `{{char_name}}` / `{{user_name}}`。"""
    return _MACRO_PATTERN.sub(lambda m: "{{" + _ST_MACROS[m.group(1).lower()] + "}}", text)


def _clip(text: str, limit: int, *, field: str, name: str, warnings: list[str]) -> str:
    """超长即截断并记一条警告（persona 层不受预算裁剪，静默超长会吃掉上下文）。"""
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean
    warnings.append(f"角色「{name}」的{field}超过 {limit} 字，已截断")
    return clean[:limit]


def _join_blocks(*blocks: str) -> str:
    """按顺序拼接非空段落（空段不留下空行）。"""
    return "\n\n".join(block.strip() for block in blocks if block and block.strip())


def _first_line(text: str) -> str:
    """取首行非空文本（酒馆卡没有「一句话定位」，只能就地取材）。"""
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def _setting_sources(character: DataSourceCharacter) -> str:
    """角色卡里承载「设定」的全部字段拼在一起（空串 = 这张卡没有设定字段）。

    实测：有一部分卡（本机 4/4）`description` / `personality` / `scenario` 全空，
    「是谁、怎么说话」全在开场白里，而设定全在内嵌世界书里。
    """
    return _join_blocks(
        character.system_prompt,
        character.description,
        character.personality,
        character.scenario,
        character.post_history_instructions,
    )


def _clean_tags(values: object, warnings: list[str], name: str) -> list[str]:
    """标签清洗：与自建角色同一套上限（条数 + 单条长度）。"""
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        if len(text) > _MAX_TAG_CHARS:
            text = text[:_MAX_TAG_CHARS]
        if text not in out:
            out.append(text)
    if len(out) > _MAX_TAG_ITEMS:
        warnings.append(f"角色「{name}」的标签超过 {_MAX_TAG_ITEMS} 条，已截断")
        out = out[: _MAX_TAG_ITEMS]
    return out


def _persona_from_character(
    character: DataSourceCharacter, warnings: list[str]
) -> PersonaPreset:
    """单个角色卡 → persona（字段搬家 + 宏对齐 + 身份兜底 + 上限截断）。"""
    name = (character.name or "").strip() or "未命名角色"

    # 先对齐宏再拼：否则「正文里有 {{char}}」会被当成「没提名字」而多补一句身份
    # 人设正文 = 「是谁 / 怎么说话」；背景 = 「过往与世界观」。
    # 与 PersonaPreset 的分块语义对齐（见 app/prompts/persona/loader.py）。
    prompt = align_macros(
        _join_blocks(character.system_prompt, character.description, character.personality)
    )
    background = align_macros(
        _join_blocks(character.scenario, character.post_history_instructions)
    )
    if not (prompt or background):
        # 设定字段全空时拿开场白充当人设正文：这张卡的「声音」就在这里，
        # 直接丢掉的话用户会得到「读到了却一个角色都没有」；
        # 真正的设定仍由该角色 scope 下的世界书条目负责。
        warnings.append(f"角色「{name}」没有设定字段，人设正文取自开场白")
        prompt = align_macros(character.first_message)

    # 角色名必须进提示词：内置人设都以「你是苏澄，…」开头，而酒馆卡（尤其是
    # description 全空、正文取自开场白的那种）常常通篇不提自己的名字——
    # 不补这一句，模型不知道自己在扮演谁，用户问「你叫什么」就只能靠编。
    body = prompt  # 加身份句之前的正文：标题用它，免得清单一排「你是{{char_name}}。」
    if "{{char_name}}" not in prompt and name not in prompt:
        prompt = f"{_IDENTITY_LINE}\n\n{prompt}".strip()

    description = (character.description or "").strip()
    if len(description) > _MAX_DESCRIPTION:
        description = description[:_MAX_DESCRIPTION]
    # 一句话定位：简介首行 → 人设正文首行 → 兜底
    title = _first_line(description) or _first_line(body) or "来自酒馆的角色卡"

    return PersonaPreset(
        id=persona_id_for(character),
        name=_clip(name, _MAX_NAME, field="角色名", name=name, warnings=warnings),
        title=_clip(title, _MAX_TITLE, field="一句话定位", name=name, warnings=warnings),
        description=description,
        tags=_clean_tags(character.extra.get("tags"), warnings, name),
        creator=str(character.extra.get("creator") or "").strip() or "tavern",
        prompt=_clip(
            prompt, _MAX_PROMPT, field="人设正文", name=name, warnings=warnings
        ),
        background=_clip(
            background,
            _MAX_BACKGROUND,
            field="背景故事",
            name=name,
            warnings=warnings,
        ),
        variables=[],
    )


def personas_from_snapshot(
    snapshot: DataSourceSnapshot,
) -> tuple[list[PersonaPreset], list[str]]:
    """把一次数据源读取的角色卡映射为 persona，返回 (角色, 警告)。"""
    presets: list[PersonaPreset] = []
    warnings: list[str] = []
    unused: list[str] = []

    for character in snapshot.characters:
        has_setting = bool(_setting_sources(character))
        if not has_setting and not (character.first_message or "").strip():
            # 设定与开场白都空 → 这个角色没有可注入的内容，出现了也只是个空壳
            warnings.append(f"角色「{character.name}」设定与开场白全空，已跳过")
            continue
        for field, label in _UNUSED_FIELDS:
            if field == "first_message" and not has_setting:
                continue  # 它正在当人设正文用，不算「未使用」
            if getattr(character, field, None):
                unused.append(label)
                break
        presets.append(_persona_from_character(character, warnings))

    if unused:
        warnings.append(
            "部分角色卡的" + " / ".join(sorted(set(unused))) + "未使用"
            "（桌宠对话从用户这一句开始，不注入开场白与示例对话）"
        )
    return presets, warnings


def collect_datasource_personas() -> tuple[list[PersonaPreset], list[str]]:
    """聚合**启用插件**贡献的全部角色卡 → persona，返回 (角色, 警告)。"""
    snapshots, warnings = read_snapshots()
    personas: list[PersonaPreset] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        presets, source_warnings = personas_from_snapshot(snapshot)
        warnings.extend(source_warnings)
        for preset in presets:
            if preset.id in seen:  # 同一角色卡被两个数据源读到：留第一份
                continue
            seen.add(preset.id)
            personas.append(preset)
    return personas, warnings
