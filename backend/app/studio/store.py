"""用户创作内容（角色卡 / 世界书条目 / 文风）的本地存储门面。

## 目录约定

    <root>/                              # 默认 backend/data/studio（backend/data/ 已被 .gitignore 覆盖）
        personas/<id>.yaml               # 我创建的角色卡（与内置角色同构）
        personas/deleted_builtin.json    # 我从当前工坊隐藏的内置角色 id（不改动内置文件）
        worldbook/<id>.yaml              # 我创建的世界书条目（与内置条目同构）
        worldbook/disabled_builtin.json  # 我停用掉的内置条目 id（不改动内置文件）
        worldbook/deleted_builtin.json   # 我从当前工坊隐藏的内置条目 id（不改动内置文件）
        styles/<id>.yaml                 # 我创建的文风预设（与内置文风同构）
        styles/deleted_builtin.json      # 我从当前工坊隐藏的内置文风 id（不改动内置文件）

## 四条关键约定

1. **与内置同构**：用户内容与内置资源共用同一套模型（PersonaPreset /
   WorldBookEntry / StylePreset），因此自建角色在对话链路里与内置角色零差异——
   加载器只是多扫一个目录。这样「用户能做的」与「内置能做的」不会分叉成两套逻辑。
2. **内置资源不改包文件**：内置资源在包目录里（docker 镜像中可能只读），且属项目原创内容。
   用户可以在工坊删除它；删除的语义是写入用户侧隐藏清单，内置文件一字未改，之后不再出现在工坊
   与对话链路中。用户也可以用同样的用户侧机制停用内置世界书条目。
3. **id 不可改**：角色 id 同时是记忆隔离命名空间（温层 collection 名
   `memory_{id}`、冷层 companion 字段、个人记忆分库），改 id 等于换一整套记忆，
   所以创建后不允许修改；id 由系统自动生成（`user-` 前缀）。
4. **合规**：用户自撰内容属**本地数据**，只落 backend/data/，不进仓库、
   不随发行物分发（AGENTS.md §6）。

## 容错取向

读接口对坏文件**宽容**：单个用户文件写坏（YAML 语法错、id 与文件名不符）只跳过
它并返回警告，不阻断整份清单——用户手改文件是常态，一个坏文件不该让所有角色都
用不了。写接口相反，**宁可报错也不写入可疑数据**（非法正则、空的必填项、不存在的
归属角色一律拒绝）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from app.paths import data_path
from app.prompts.persona.loader import (
    PersonaPreset,
    load_builtin_presets,
    load_preset_file,
)
from app.prompts.style.loader import load_builtin_styles, load_style_file
from app.prompts.style.models import StyleExample, StylePreset
from app.worldbook.loader import load_builtin_entries, load_entry_file
from app.worldbook.models import SCOPE_ALL, WorldBookEntry

logger = logging.getLogger(__name__)


def default_root() -> Path:
    """用户内容目录的默认位置（可写数据目录）。"""
    return data_path("data", "studio")

#: 用户内容 id 允许的字符集：小写字母或数字开头，其后允许 - _。
#: 与角色 id、Qdrant collection 名（`memory_{companion_id}`）的约束保持一致。
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

#: 用户内容 id 前缀：一眼区分「我的」与「内置」
_ID_PREFIX = "user-"

#: 从标题里提取 ascii slug 用的片段匹配（中文标题会得到空串）
_SLUG_PATTERN = re.compile(r"[a-z0-9]+")

#: 外部只读角色来源：返回一批 `PersonaPreset`。
#: 传可调用对象而不是现成列表——数据源内容会随插件启停 / 配置变更而变。
ExternalPersonaSource = Callable[[], Iterable[PersonaPreset]]

#: 内置资源的用户侧状态文件（包内 YAML 一律不修改）
_DISABLED_FILE = "disabled_builtin.json"
_DELETED_PERSONAS_FILE = "deleted_builtin.json"
_DELETED_ENTRIES_FILE = "deleted_builtin.json"
#: 文风与角色、条目各在自己的目录下，因此可以复用同一个文件名
_DELETED_STYLES_FILE = "deleted_builtin.json"

# ---- 字段长度上限 ----
# 为什么要限：人设层在 PromptManager 里是**必留层**（不参与裁剪），用户把一篇小说
# 粘进人设正文会直接把上下文撑爆；报错比让对话悄悄失控好。
# 文风同理：风格指令块排在 system 末尾、示例对话还会以 few-shot 形式进消息列表，
# 不限长的话「自定义文风」就成了把上下文塞满的快捷方式。
_MAX_PERSONA_NAME = 50
_MAX_PERSONA_TITLE = 100
_MAX_PERSONA_DESCRIPTION = 500
_MAX_PERSONA_PROMPT = 8000
_MAX_PERSONA_BACKGROUND = 8000
_MAX_TAG_ITEMS = 20
_MAX_TAG_CHARS = 30
_MAX_VARIABLE_ITEMS = 40
_MAX_VARIABLE_CHARS = 40
_MAX_ENTRY_TITLE = 100
_MAX_ENTRY_CONTENT = 4000
_MAX_ENTRY_VECTOR_TEXT = 500
_MAX_TRIGGER_ITEMS = 50
_MAX_TRIGGER_ITEM_CHARS = 100
_MAX_STYLE_NAME = 40
_MAX_STYLE_DESCRIPTION = 200
_MAX_STYLE_PROMPT = 4000
_MAX_STYLE_AVOID_ITEMS = 20
_MAX_STYLE_AVOID_CHARS = 60
_MAX_STYLE_EXAMPLES = 6
_MAX_STYLE_EXAMPLE_CHARS = 500
_MAX_STYLE_CONFLICT_ITEMS = 20
_MAX_STYLE_CONFLICT_CHARS = 30

#: 采样参数白名单与取值范围。
#:
#: 为什么要白名单而不是照抄用户给的 dict：`StylePreset.sampling` 的优先级**最高**
#: （盖过模型预设档），写错一个键不会报错、只会静默无效；写错一个量级（如
#: temperature: 80）则会让每一轮对话都退化。取值区间对齐 `resolve_sampling`
#: 真正消费的那几个键（见 app/llm/profiles.py）。
_SAMPLING_KEYS: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 2.0),
    "max_tokens": (1.0, 8192.0),
    "top_p": (0.0, 1.0),
    "frequency_penalty": (-2.0, 2.0),
    "presence_penalty": (-2.0, 2.0),
}

#: 采样参数白名单的**对外出口**：界面据此渲染输入框与即时校验，
#: 免得前端把键名与区间硬编码一份（两边漂移了用户看到的就是假提示）
SAMPLING_SPEC: list[dict[str, object]] = [
    {"key": key, "min": low, "max": high} for key, (low, high) in _SAMPLING_KEYS.items()
]

#: 字段长度上限的**对外出口**：接口 /chat/studio/meta 用它给界面做即时校验提示，
#: 免得前端把同一批数字硬编码一份（两边漂移了用户看到的就是假提示）
LIMITS: dict[str, int] = {
    "persona_name": _MAX_PERSONA_NAME,
    "persona_title": _MAX_PERSONA_TITLE,
    "persona_description": _MAX_PERSONA_DESCRIPTION,
    "persona_prompt": _MAX_PERSONA_PROMPT,
    "persona_background": _MAX_PERSONA_BACKGROUND,
    "tag_items": _MAX_TAG_ITEMS,
    "tag_chars": _MAX_TAG_CHARS,
    "variable_items": _MAX_VARIABLE_ITEMS,
    "variable_chars": _MAX_VARIABLE_CHARS,
    "entry_title": _MAX_ENTRY_TITLE,
    "entry_content": _MAX_ENTRY_CONTENT,
    "entry_vector_text": _MAX_ENTRY_VECTOR_TEXT,
    "trigger_items": _MAX_TRIGGER_ITEMS,
    "trigger_chars": _MAX_TRIGGER_ITEM_CHARS,
    "style_name": _MAX_STYLE_NAME,
    "style_description": _MAX_STYLE_DESCRIPTION,
    "style_prompt": _MAX_STYLE_PROMPT,
    "style_avoid_items": _MAX_STYLE_AVOID_ITEMS,
    "style_avoid_chars": _MAX_STYLE_AVOID_CHARS,
    "style_examples": _MAX_STYLE_EXAMPLES,
    "style_example_chars": _MAX_STYLE_EXAMPLE_CHARS,
    "style_conflict_items": _MAX_STYLE_CONFLICT_ITEMS,
    "style_conflict_chars": _MAX_STYLE_CONFLICT_CHARS,
}


class StudioError(ValueError):
    """创作工坊的输入/状态错误（接口层据类型转 400 / 403 / 404）。"""


class BuiltinReadOnlyError(StudioError):
    """试图修改内置（只读）资源。"""


@dataclass(frozen=True)
class PersonaSummary:
    """角色清单项（**不含**人设正文；要正文请取详情）。"""

    id: str
    name: str
    title: str
    description: str
    tags: list[str]
    builtin: bool

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "builtin": self.builtin,
        }


@dataclass(frozen=True)
class EntrySummary:
    """世界书条目清单项。

    含正文：用户要编辑自己的条目，而内置条目展示出来也有参照价值
    （内置世界书是项目原创设定，体量小，不涉及第三方内容）。
    """

    entry: WorldBookEntry
    builtin: bool

    def as_dict(self) -> dict:
        return {**self.entry.model_dump(), "builtin": self.builtin}


@dataclass(frozen=True)
class StyleSummary:
    """文风预设清单项（**不含**风格指令与示例正文；要正文请取详情）。

    与 `PersonaSummary` 同一取向：列表接口只回展示必需的信息。文风正文加上
    示例对话动辄上千字，内置 + 用户的清单全量返回会让「打开工坊」这一下变重。
    """

    id: str
    name: str
    description: str
    tags: list[str]
    examples: int
    builtin: bool

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            # 叫 example_count 而不是 examples：详情接口的 `examples` 是示例对话**数组**，
            # 同名不同型会让调用方（尤其前端）在两条接口之间错用而不报错。
            "example_count": self.examples,
            "builtin": self.builtin,
        }


# ---------- 落盘字段（顺序即 YAML 中的书写顺序，便于用户手改）----------


def _persona_payload(preset: PersonaPreset) -> dict:
    return {
        "id": preset.id,
        "name": preset.name,
        "title": preset.title,
        "description": preset.description,
        "tags": list(preset.tags),
        "background": preset.background,
        "creator": preset.creator,
        "prompt": preset.prompt,
        "variables": list(preset.variables),
    }


def _entry_payload(entry: WorldBookEntry) -> dict:
    return {
        "id": entry.id,
        "title": entry.title,
        "scope": entry.scope,
        "enabled": entry.enabled,
        "priority": entry.priority,
        "keys": list(entry.keys),
        "regex": list(entry.regex),
        "case_sensitive": entry.case_sensitive,
        "vector_text": entry.vector_text,
        "vector_threshold": entry.vector_threshold,
        "content": entry.content,
    }


def _style_payload(preset: StylePreset) -> dict:
    """落盘字段（顺序即 YAML 书写顺序，与内置文风预设保持同一版式）。"""
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
    }


class _BlockStyleDumper(yaml.SafeDumper):
    """让多行字符串输出为 `|` 块标量（角色卡正文手改友好）。

    单行值仍走默认风格。块标量对「行尾空格」等情形不被 YAML 允许时，
    PyYAML 的 emitter 会自动回退到其它风格——这里不必自己判断。
    """


def _represent_multiline_str(dumper: yaml.SafeDumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockStyleDumper.add_representer(str, _represent_multiline_str)


# ---------- 取值清洗与校验 ----------


def _require_text(value: str | None, label: str, *, max_chars: int = 0) -> str:
    """必填文本：去空白后不得为空；给了 max_chars 则同时限制长度。"""
    text = (value or "").strip()
    if not text:
        raise StudioError(f"{label}不能为空")
    if max_chars and len(text) > max_chars:
        raise StudioError(f"{label}最多 {max_chars} 字，当前 {len(text)} 字")
    return text


def _optional_text(value: str | None, label: str, *, max_chars: int) -> str:
    """可选文本（空串合法）：去空白后限制长度。"""
    text = (value or "").strip()
    if len(text) > max_chars:
        raise StudioError(f"{label}最多 {max_chars} 字，当前 {len(text)} 字")
    return text


def _clean_list(
    values: list[str] | None,
    *,
    label: str = "条目",
    max_items: int = 0,
    max_chars: int = 0,
) -> list[str]:
    """清洗列表：去掉空白项与重复项（保留原顺序），并按需限制条数与单项长度。"""
    result: list[str] = []
    for item in values or []:
        text = str(item).strip()
        if not text or text in result:
            continue
        if max_chars and len(text) > max_chars:
            raise StudioError(f"{label}「{text[:20]}」超过 {max_chars} 字")
        result.append(text)
    if max_items and len(result) > max_items:
        raise StudioError(f"{label}最多 {max_items} 条，当前 {len(result)} 条")
    return result


def _slugify(text: str, *, limit: int = 24) -> str:
    """从标题提取 ascii slug（中文标题得到空串 → 由调用方回退随机后缀）。"""
    return "-".join(_SLUG_PATTERN.findall((text or "").lower()))[:limit].strip("-")


def _clean_sampling(values: dict[str, float] | None) -> dict[str, float]:
    """校验文风预设的建议采样参数（键必须白名单内，值必须在合理区间）。

    宁可报错也不写入可疑值：文风预设的 sampling 优先级最高，会盖过模型预设档，
    一个 `temperatur`（拼错）或 `temperature: 80` 不会被任何下游拦住，
    只会让这个文风的每一轮对话都变得不可用，而用户完全看不出原因。
    """
    cleaned: dict[str, float] = {}
    for key, value in (values or {}).items():
        name = str(key).strip()
        if name not in _SAMPLING_KEYS:
            allowed = "、".join(_SAMPLING_KEYS)
            raise StudioError(f"不支持的采样参数「{name}」；可用：{allowed}")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise StudioError(f"采样参数「{name}」必须是数字，当前是 {value!r}") from exc
        low, high = _SAMPLING_KEYS[name]
        if not low <= number <= high:
            raise StudioError(f"采样参数「{name}」应在 {low} ~ {high} 之间，当前是 {number}")
        cleaned[name] = number
    return cleaned


def _clean_examples(values: list[dict] | None) -> list[StyleExample]:
    """校验示例对话：每组都必须同时有 user 与 assistant（空组毫无示范价值）。"""
    examples: list[StyleExample] = []
    for index, item in enumerate(values or [], start=1):
        if not isinstance(item, dict):
            raise StudioError(f"第 {index} 组示例对话格式不对（应为 user / assistant 两个字段）")
        user = _require_text(
            str(item.get("user") or ""),
            f"第 {index} 组示例的「用户发言」",
            max_chars=_MAX_STYLE_EXAMPLE_CHARS,
        )
        assistant = _require_text(
            str(item.get("assistant") or ""),
            f"第 {index} 组示例的「目标回应」",
            max_chars=_MAX_STYLE_EXAMPLE_CHARS,
        )
        examples.append(StyleExample(user=user, assistant=assistant))
    if len(examples) > _MAX_STYLE_EXAMPLES:
        raise StudioError(f"示例对话最多 {_MAX_STYLE_EXAMPLES} 组，当前 {len(examples)} 组")
    return examples


class StudioStore:
    """用户创作内容的存储门面（内置不可直接改 + 用户目录可写）。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        builtin_personas: dict[str, PersonaPreset] | None = None,
        builtin_entries: list[WorldBookEntry] | None = None,
        builtin_styles: dict[str, StylePreset] | None = None,
        external_personas: ExternalPersonaSource | None = None,
    ) -> None:
        """构造门面。

        参数:
            root: 用户内容目录；缺省用 default_root()（开发形态下是 backend/data/studio）；
            builtin_personas / builtin_entries / builtin_styles: 内置资源；缺省时从包目录
                加载（注入是为了测试可控——测试用假的「内置」，绝不读真实包目录）；
            external_personas: **外部只读角色来源**（插件数据源 → persona）。
                传可调用对象而不是现成列表：数据源内容会随插件启停 / 配置变更而变，
                每次都重取，存储层不必知道什么时候该失效。
                这个缝开在这里而不是各调用点：`list_personas` / `all_personas` /
                `get_persona` / 世界书 scope 校验**必须看到同一份清单**，
                在两个地方各合并一次，迟早变成两个不一样的世界。
        """
        self._root = Path(root) if root is not None else default_root()
        self._injected_personas = builtin_personas
        self._injected_entries = builtin_entries
        self._injected_styles = builtin_styles
        self._external_personas = external_personas
        self._personas_cache: dict[str, PersonaPreset] | None = None
        self._entries_cache: list[WorldBookEntry] | None = None
        self._styles_cache: dict[str, StylePreset] | None = None

    # ---------- 路径 ----------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def persona_dir(self) -> Path:
        """我的角色卡目录。"""
        return self._root / "personas"

    @property
    def entry_dir(self) -> Path:
        """我的世界书条目目录。"""
        return self._root / "worldbook"

    @property
    def style_dir(self) -> Path:
        """我的文风预设目录。"""
        return self._root / "styles"

    def _validate_id(self, value: str, *, label: str) -> str:
        """校验 id 并防目录穿越（拒绝 `..` 与非法字符）。"""
        if not isinstance(value, str) or not _ID_PATTERN.match(value) or ".." in value:
            raise StudioError(
                f"非法的{label} id：{value!r}"
                "（只允许小写字母/数字/-/_，且以字母或数字开头）"
            )
        return value

    def _persona_path(self, persona_id: str) -> Path:
        return self.persona_dir / f"{self._validate_id(persona_id, label='角色')}.yaml"

    def _entry_path(self, entry_id: str) -> Path:
        return self.entry_dir / f"{self._validate_id(entry_id, label='世界书条目')}.yaml"

    def _style_path(self, style_id: str) -> Path:
        return self.style_dir / f"{self._validate_id(style_id, label='文风')}.yaml"

    def _disabled_path(self) -> Path:
        return self.entry_dir / _DISABLED_FILE

    def _deleted_personas_path(self) -> Path:
        return self.persona_dir / _DELETED_PERSONAS_FILE

    def _deleted_entries_path(self) -> Path:
        return self.entry_dir / _DELETED_ENTRIES_FILE

    def _deleted_styles_path(self) -> Path:
        return self.style_dir / _DELETED_STYLES_FILE

    # ---------- 内置资源（进程内缓存：包目录内容不会在运行期变）----------

    def _builtin_personas(self) -> dict[str, PersonaPreset]:
        if self._personas_cache is None:
            self._personas_cache = (
                dict(self._injected_personas)
                if self._injected_personas is not None
                else load_builtin_presets()
            )
        return self._personas_cache

    def _builtin_entries(self) -> list[WorldBookEntry]:
        if self._entries_cache is None:
            self._entries_cache = (
                list(self._injected_entries)
                if self._injected_entries is not None
                else load_builtin_entries()
            )
        return self._entries_cache

    def _builtin_styles(self) -> dict[str, StylePreset]:
        if self._styles_cache is None:
            self._styles_cache = (
                dict(self._injected_styles)
                if self._injected_styles is not None
                else load_builtin_styles()
            )
        return self._styles_cache

    # ---------- 用户文件读取（宽松：坏文件跳过 + 记警告）----------

    def _load_user_personas(self) -> tuple[list[PersonaPreset], list[str]]:
        items: list[PersonaPreset] = []
        warnings: list[str] = []
        for path in sorted(self.persona_dir.glob("*.yaml")):
            try:
                preset = load_preset_file(path)
            except Exception as exc:  # noqa: BLE001 - 单个坏文件不该拖垮整份清单
                warnings.append(f"角色文件 {path.name} 解析失败，已跳过：{exc}")
                continue
            # 文件内 id 必须与文件名一致：否则按 id 定位文件会失效
            # （删除会删不到、改名会变成两个角色），宁可跳过并让用户看到警告
            if preset.id != path.stem:
                warnings.append(
                    f"角色文件 {path.name} 里的 id（{preset.id}）与文件名不一致，已跳过"
                )
                continue
            items.append(preset)
        return items, warnings

    def _load_user_entries(self) -> tuple[list[WorldBookEntry], list[str]]:
        items: list[WorldBookEntry] = []
        warnings: list[str] = []
        for path in sorted(self.entry_dir.glob("*.yaml")):
            try:
                entry = load_entry_file(path)
            except Exception as exc:  # noqa: BLE001 - 同上
                warnings.append(f"世界书文件 {path.name} 解析失败，已跳过：{exc}")
                continue
            if entry.id != path.stem:
                warnings.append(
                    f"世界书文件 {path.name} 里的 id（{entry.id}）与文件名不一致，已跳过"
                )
                continue
            items.append(entry)
        return items, warnings

    def _load_user_styles(self) -> tuple[list[StylePreset], list[str]]:
        items: list[StylePreset] = []
        warnings: list[str] = []
        for path in sorted(self.style_dir.glob("*.yaml")):
            try:
                preset = load_style_file(path)
            except Exception as exc:  # noqa: BLE001 - 单个坏文件不该拖垮整份清单
                warnings.append(f"文风文件 {path.name} 解析失败，已跳过：{exc}")
                continue
            if preset.id != path.stem:
                warnings.append(
                    f"文风文件 {path.name} 里的 id（{preset.id}）与文件名不一致，已跳过"
                )
                continue
            items.append(preset)
        return items, warnings

    # ---------- 用户侧内置资源状态 ----------

    def _read_id_set(self, path: Path) -> set[str]:
        """读取用户侧内置资源状态；缺失/损坏按空集处理。"""
        if not path.is_file():
            return set()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("内置资源状态读取失败，按空集处理：%s", path)
            return set()
        ids = raw.get("ids") if isinstance(raw, dict) else None
        return {str(item) for item in ids if str(item).strip()} if isinstance(ids, list) else set()

    def _write_id_set(self, path: Path, ids: set[str]) -> None:
        if ids:
            self._write_json(path, {"ids": sorted(ids)})
        else:
            path.unlink(missing_ok=True)

    def deleted_builtin_persona_ids(self) -> set[str]:
        """我从当前工坊隐藏的内置角色 id。"""
        return self._read_id_set(self._deleted_personas_path())

    def deleted_builtin_entry_ids(self) -> set[str]:
        """我从当前工坊隐藏的内置条目 id。"""
        return self._read_id_set(self._deleted_entries_path())

    def deleted_builtin_style_ids(self) -> set[str]:
        """我从当前工坊隐藏的内置文风 id。"""
        return self._read_id_set(self._deleted_styles_path())

    def _delete_builtin_persona(self, persona_id: str) -> None:
        ids = self.deleted_builtin_persona_ids()
        ids.add(persona_id)
        self._write_id_set(self._deleted_personas_path(), ids)

    def _delete_builtin_entry(self, entry_id: str) -> None:
        ids = self.deleted_builtin_entry_ids()
        ids.add(entry_id)
        self._write_id_set(self._deleted_entries_path(), ids)

    def _delete_builtin_style(self, style_id: str) -> None:
        ids = self.deleted_builtin_style_ids()
        ids.add(style_id)
        self._write_id_set(self._deleted_styles_path(), ids)

    # ---------- 合并视图 ----------

    def _merged_personas(self) -> tuple[list[tuple[PersonaPreset, bool]], list[str]]:
        """内置在前、我的在中、**外部来源在最后**；同名时先到的优先（并记警告）。

        外部来源（插件数据源 → persona）以「内置」身份并入：它本来就不是用户的文件，
        因此工作台里**不可原地编辑**，要改只能「复制到我的」；但可以**删除**——
        删除只是写进用户侧隐藏清单（`deleted_builtin.json`），不触碰来源文件。
        """
        builtin = self._builtin_personas()
        deleted = self.deleted_builtin_persona_ids()
        items: list[tuple[PersonaPreset, bool]] = [
            (preset, True) for preset in builtin.values() if preset.id not in deleted
        ]
        seen = set(builtin)
        user, warnings = self._load_user_personas()
        for preset in user:
            if preset.id in seen:
                warnings.append(f"角色 {preset.id} 与内置角色同名，已忽略用户文件")
                continue
            seen.add(preset.id)
            items.append((preset, False))
        for preset in self._external_personas() if self._external_personas else ():
            if preset.id in seen:
                warnings.append(f"角色 {preset.id} 与已有角色 id 冲突，已忽略外部来源")
                continue
            seen.add(preset.id)
            if preset.id in deleted:
                continue
            items.append((preset, True))
        return items, warnings

    def _merged_entries(self) -> tuple[list[tuple[WorldBookEntry, bool]], list[str]]:
        """内置在前、我的在后；同名时内置优先（并记警告）。"""
        disabled = self.disabled_builtin_ids()
        deleted = self.deleted_builtin_entry_ids()
        items: list[tuple[WorldBookEntry, bool]] = []
        for entry in self._builtin_entries():
            if entry.id in deleted:
                continue
            # 停用偏好只改「合并视图」里的 enabled，内置对象本身不动
            shown = (
                entry.model_copy(update={"enabled": False})
                if entry.id in disabled and entry.enabled
                else entry
            )
            items.append((shown, True))
        builtin_ids = {entry.id for entry, _ in items}
        user, warnings = self._load_user_entries()
        for entry in user:
            if entry.id in builtin_ids:
                warnings.append(f"世界书条目 {entry.id} 与内置条目同名，已忽略用户文件")
                continue
            items.append((entry, False))
        return items, warnings

    def _merged_styles(self) -> tuple[list[tuple[StylePreset, bool]], list[str]]:
        """内置在前、我的在后；同名时内置优先（并记警告）。

        与角色 / 条目同一套语义：内置文风不可原地改（要改先「复制为我的」），
        但可以从当前工坊删除——删除只写用户侧隐藏清单，包内 YAML 一字未改。
        """
        deleted = self.deleted_builtin_style_ids()
        items: list[tuple[StylePreset, bool]] = [
            (preset, True)
            for preset in self._builtin_styles().values()
            if preset.id not in deleted
        ]
        builtin_ids = {preset.id for preset, _ in items}
        user, warnings = self._load_user_styles()
        for preset in user:
            if preset.id in builtin_ids:
                warnings.append(f"文风 {preset.id} 与内置文风同名，已忽略用户文件")
                continue
            items.append((preset, False))
        return items, warnings

    def list_personas(self) -> tuple[list[PersonaSummary], list[str]]:
        """角色清单（内置 + 我的），以及读取过程中的警告。"""
        merged, warnings = self._merged_personas()
        summaries = [
            PersonaSummary(
                id=preset.id,
                name=preset.name,
                title=preset.title,
                description=preset.description,
                tags=list(preset.tags),
                builtin=builtin,
            )
            for preset, builtin in merged
        ]
        return summaries, warnings

    def all_personas(self) -> tuple[dict[str, PersonaPreset], list[str]]:
        """角色全集的 id 索引（对话链路用），以及读取过程中的警告。"""
        merged, warnings = self._merged_personas()
        return {preset.id: preset for preset, _ in merged}, warnings

    def list_entries(self) -> tuple[list[EntrySummary], list[str]]:
        """世界书条目清单（内置 + 我的，已应用停用偏好），以及警告。"""
        merged, warnings = self._merged_entries()
        return [EntrySummary(entry=entry, builtin=builtin) for entry, builtin in merged], warnings

    def all_entries(self) -> tuple[list[WorldBookEntry], list[str]]:
        """世界书条目全集（对话链路用），以及读取过程中的警告。"""
        merged, warnings = self._merged_entries()
        return [entry for entry, _ in merged], warnings

    def list_styles(self) -> tuple[list[StyleSummary], list[str]]:
        """文风预设清单（内置 + 我的），以及读取过程中的警告。"""
        merged, warnings = self._merged_styles()
        summaries = [
            StyleSummary(
                id=preset.id,
                name=preset.name,
                description=preset.description,
                tags=list(preset.tags),
                examples=preset.example_count,
                builtin=builtin,
            )
            for preset, builtin in merged
        ]
        return summaries, warnings

    def all_styles(self) -> tuple[dict[str, StylePreset], list[str]]:
        """文风预设全集的 id 索引（对话链路用），以及读取过程中的警告。"""
        merged, warnings = self._merged_styles()
        return {preset.id: preset for preset, _ in merged}, warnings

    # ---------- 角色：详情与写入 ----------

    def get_persona(self, persona_id: str) -> tuple[PersonaPreset, bool]:
        """取单个角色 → (角色, 是否内置)；不存在抛 FileNotFoundError。

        查的是**合并视图**而不是「内置字典 + 用户文件」两处：外部来源（插件数据源
        的角色）既不在包目录、也没有用户文件，少看它就会让详情页 / 只读保护 /
        复制全部失效——而 `list_personas` 里又确实看得到它。

        先校验 id：非法 id（含 `..` 目录穿越）仍然报 `StudioError`，不因为
        「反正只是查表」就把守卫省掉——调用方靠这两个异常区分「非法输入」与「不存在」。
        """
        self._validate_id(persona_id, label="角色")
        for preset, builtin in self._merged_personas()[0]:
            if preset.id == persona_id:
                return preset, builtin
        raise FileNotFoundError(f"角色不存在：{persona_id}")

    def create_persona(
        self,
        *,
        name: str,
        prompt: str,
        title: str = "",
        description: str = "",
        background: str = "",
        tags: list[str] | None = None,
        variables: list[str] | None = None,
        creator: str = "user",
    ) -> PersonaPreset:
        """创建我的角色（id 自动生成，`user-` 前缀）。"""
        clean_name = _require_text(name, "角色名", max_chars=_MAX_PERSONA_NAME)
        clean_prompt = _require_text(prompt, "人设正文", max_chars=_MAX_PERSONA_PROMPT)
        persona_id = self._allocate_id(_slugify(title or clean_name))
        preset = PersonaPreset(
            id=persona_id,
            name=clean_name,
            title=_optional_text(title, "一句话定位", max_chars=_MAX_PERSONA_TITLE),
            description=_optional_text(
                description, "角色简介", max_chars=_MAX_PERSONA_DESCRIPTION
            ),
            tags=_clean_list(
                tags, label="标签", max_items=_MAX_TAG_ITEMS, max_chars=_MAX_TAG_CHARS
            ),
            creator=creator,
            prompt=clean_prompt,
            background=_optional_text(
                background, "背景故事", max_chars=_MAX_PERSONA_BACKGROUND
            ),
            variables=_clean_list(
                variables,
                label="状态变量名",
                max_items=_MAX_VARIABLE_ITEMS,
                max_chars=_MAX_VARIABLE_CHARS,
            ),
        )
        return self._write_persona(preset)

    def update_persona(
        self,
        persona_id: str,
        *,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        prompt: str | None = None,
        background: str | None = None,
        tags: list[str] | None = None,
        variables: list[str] | None = None,
    ) -> PersonaPreset:
        """更新我的角色卡（内置角色 → BuiltinReadOnlyError）。

        `None` = 不修改该字段；要清空请传空字符串（`name` / `prompt` 不接受清空）。
        **id 不可修改**：它是记忆隔离命名空间，改 id 等于换一整套记忆。
        """
        current, builtin = self.get_persona(persona_id)
        if builtin:
            raise BuiltinReadOnlyError(
                f"「{current.name}」是内置角色，不能直接修改；"
                "请用「复制为我的角色」得到一份可编辑的副本"
            )
        changes: dict[str, object] = {}
        if name is not None:
            changes["name"] = _require_text(name, "角色名", max_chars=_MAX_PERSONA_NAME)
        if prompt is not None:
            changes["prompt"] = _require_text(
                prompt, "人设正文", max_chars=_MAX_PERSONA_PROMPT
            )
        if title is not None:
            changes["title"] = _optional_text(title, "一句话定位", max_chars=_MAX_PERSONA_TITLE)
        if description is not None:
            changes["description"] = _optional_text(
                description, "角色简介", max_chars=_MAX_PERSONA_DESCRIPTION
            )
        if background is not None:
            changes["background"] = _optional_text(
                background, "背景故事", max_chars=_MAX_PERSONA_BACKGROUND
            )
        if tags is not None:
            changes["tags"] = _clean_list(
                tags, label="标签", max_items=_MAX_TAG_ITEMS, max_chars=_MAX_TAG_CHARS
            )
        if variables is not None:
            changes["variables"] = _clean_list(
                variables,
                label="状态变量名",
                max_items=_MAX_VARIABLE_ITEMS,
                max_chars=_MAX_VARIABLE_CHARS,
            )

        # 走 model_validate 而不是 model_copy：后者绕过 pydantic 校验
        updated = PersonaPreset.model_validate({**current.model_dump(), **changes})
        return self._write_persona(updated)

    def duplicate_persona(self, persona_id: str, *, name: str | None = None) -> PersonaPreset:
        """复制一个角色为新角色（内置角色借此变成可编辑的「我的角色」）。"""
        source, _ = self.get_persona(persona_id)
        return self.create_persona(
            name=(name.strip() if name else f"{source.name}（副本）"),
            title=source.title,
            description=source.description,
            background=source.background,
            prompt=source.prompt,
            tags=list(source.tags),
            variables=list(source.variables),
        )

    def delete_persona(self, persona_id: str) -> PersonaPreset:
        """从当前工坊删除角色卡。

        用户角色删除对应的 YAML 文件；内置角色写入用户侧隐藏清单，不修改包内资源。
        本方法**只碰角色卡本身**：会话与记忆属于另外几套存储，由 API 层级联清理
        （见 `delete_bound_entries` 与 `DELETE /chat/studio/personas/{id}`）。
        """
        preset, builtin = self.get_persona(persona_id)
        if builtin:
            self._delete_builtin_persona(persona_id)
        else:
            self._persona_path(persona_id).unlink(missing_ok=True)
        return preset

    # ---------- 世界书条目：详情与写入 ----------

    def get_entry(self, entry_id: str) -> tuple[WorldBookEntry, bool]:
        """取单个条目 → (条目, 是否内置)；不存在抛 FileNotFoundError。"""
        for entry in self._builtin_entries():
            if entry.id == entry_id:
                if entry.id in self.deleted_builtin_entry_ids():
                    raise FileNotFoundError(f"世界书条目不存在：{entry_id}")
                disabled = entry.id in self.disabled_builtin_ids()
                shown = (
                    entry.model_copy(update={"enabled": False})
                    if disabled and entry.enabled
                    else entry
                )
                return shown, True
        path = self._entry_path(entry_id)
        if not path.is_file():
            raise FileNotFoundError(f"世界书条目不存在：{entry_id}")
        return load_entry_file(path), False

    def create_entry(
        self,
        *,
        title: str,
        content: str,
        keys: list[str] | None = None,
        regex: list[str] | None = None,
        vector_text: str = "",
        vector_threshold: float = 0.7,
        enabled: bool = True,
        priority: int = 0,
        scope: str = SCOPE_ALL,
        case_sensitive: bool = False,
    ) -> WorldBookEntry:
        """创建我的世界书条目（id 自动生成，`user-` 前缀）。"""
        clean_title = _require_text(title, "条目标题", max_chars=_MAX_ENTRY_TITLE)
        clean_content = _require_text(content, "条目正文", max_chars=_MAX_ENTRY_CONTENT)
        entry = WorldBookEntry(
            id=self._allocate_id(_slugify(clean_title)),
            title=clean_title,
            scope=self._validate_scope(scope),
            content=clean_content,
            keys=_clean_list(
                keys,
                label="关键词",
                max_items=_MAX_TRIGGER_ITEMS,
                max_chars=_MAX_TRIGGER_ITEM_CHARS,
            ),
            regex=_clean_list(
                regex,
                label="正则",
                max_items=_MAX_TRIGGER_ITEMS,
                max_chars=_MAX_TRIGGER_ITEM_CHARS,
            ),
            vector_text=_optional_text(
                vector_text, "语义触发文本", max_chars=_MAX_ENTRY_VECTOR_TEXT
            ),
            vector_threshold=vector_threshold,
            enabled=enabled,
            priority=priority,
            case_sensitive=case_sensitive,
        )
        self._validate_entry(entry)
        return self._write_entry(entry)

    def update_entry(
        self,
        entry_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        keys: list[str] | None = None,
        regex: list[str] | None = None,
        vector_text: str | None = None,
        vector_threshold: float | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
        scope: str | None = None,
        case_sensitive: bool | None = None,
    ) -> WorldBookEntry:
        """更新我的世界书条目（内置条目 → BuiltinReadOnlyError；改归属请用 scope）。"""
        current, builtin = self.get_entry(entry_id)
        if builtin:
            raise BuiltinReadOnlyError(
                f"「{current.title}」是内置条目，不能直接修改；"
                "可复制成我的条目，或用「启用 / 停用」开关"
            )
        changes: dict[str, object] = {}
        if title is not None:
            changes["title"] = _require_text(title, "条目标题", max_chars=_MAX_ENTRY_TITLE)
        if content is not None:
            changes["content"] = _require_text(
                content, "条目正文", max_chars=_MAX_ENTRY_CONTENT
            )
        if keys is not None:
            changes["keys"] = _clean_list(
                keys,
                label="关键词",
                max_items=_MAX_TRIGGER_ITEMS,
                max_chars=_MAX_TRIGGER_ITEM_CHARS,
            )
        if regex is not None:
            changes["regex"] = _clean_list(
                regex,
                label="正则",
                max_items=_MAX_TRIGGER_ITEMS,
                max_chars=_MAX_TRIGGER_ITEM_CHARS,
            )
        if vector_text is not None:
            changes["vector_text"] = _optional_text(
                vector_text, "语义触发文本", max_chars=_MAX_ENTRY_VECTOR_TEXT
            )
        if vector_threshold is not None:
            changes["vector_threshold"] = vector_threshold
        if enabled is not None:
            changes["enabled"] = enabled
        if priority is not None:
            changes["priority"] = priority
        if scope is not None:
            changes["scope"] = self._validate_scope(scope)
        if case_sensitive is not None:
            changes["case_sensitive"] = case_sensitive

        updated = WorldBookEntry.model_validate({**current.model_dump(), **changes})
        self._validate_entry(updated)
        return self._write_entry(updated)

    def delete_entry(self, entry_id: str) -> WorldBookEntry:
        """从当前工坊删除世界书条目。

        用户条目删除对应的 YAML 文件；内置条目写入用户侧隐藏清单，不修改包内资源。
        """
        entry, builtin = self.get_entry(entry_id)
        if builtin:
            self._delete_builtin_entry(entry_id)
        else:
            self._entry_path(entry_id).unlink(missing_ok=True)
        return entry

    def bound_entry_ids(self, persona_id: str) -> list[str]:
        """归属该角色（`scope == persona_id`）的世界书条目 id。

        删除角色时的级联清理与影响面提示共用这一份口径：两处各查一遍，
        迟早出现「提示说 1 条、实际删了 2 条」这类对不上的账。
        """
        entries, _ = self.all_entries()
        return [entry.id for entry in entries if entry.scope == persona_id]

    def count_bound_entries(self, persona_id: str) -> int:
        """有多少世界书条目**只**对该角色生效。"""
        return len(self.bound_entry_ids(persona_id))

    def delete_bound_entries(self, persona_id: str) -> list[str]:
        """删除归属该角色的**全部**世界书条目，返回被删除的 id。

        删除角色卡的级联动作：专属设定只对这个角色有意义，角色没了它们就是
        永不触发的死条目（§4.2 的「指向幽灵角色的条目永远不会触发」）。
        复用 `delete_entry`：我的条目删 YAML，内置条目写用户侧隐藏清单，
        与逐个删除的行为完全一致，不另起一套。
        """
        deleted: list[str] = []
        for entry_id in self.bound_entry_ids(persona_id):
            self.delete_entry(entry_id)
            deleted.append(entry_id)
        return deleted

    def build_draft_entry(
        self,
        *,
        title: str = "",
        content: str = "",
        keys: list[str] | None = None,
        regex: list[str] | None = None,
        vector_text: str = "",
        vector_threshold: float = 0.7,
        scope: str = SCOPE_ALL,
        case_sensitive: bool = False,
    ) -> WorldBookEntry:
        """构造一个**不入盘**的条目，清洗与校验规则与 `create_entry` 完全一致。

        界面「试触发」用它。之所以不把清洗/校验拆开暴露给接口层：两条路径
        共用同一套规则，「试的时候命中、存下来却不命中」这种不一致就不可能发生。
        """
        entry = WorldBookEntry(
            id="draft",
            title=(title or "").strip() or "草稿",
            scope=self._validate_scope(scope),
            content=(content or "").strip(),
            keys=_clean_list(
                keys,
                label="关键词",
                max_items=_MAX_TRIGGER_ITEMS,
                max_chars=_MAX_TRIGGER_ITEM_CHARS,
            ),
            regex=_clean_list(
                regex,
                label="正则",
                max_items=_MAX_TRIGGER_ITEMS,
                max_chars=_MAX_TRIGGER_ITEM_CHARS,
            ),
            vector_text=_optional_text(
                vector_text, "语义触发文本", max_chars=_MAX_ENTRY_VECTOR_TEXT
            ),
            vector_threshold=vector_threshold,
            enabled=True,
            priority=0,
            case_sensitive=case_sensitive,
        )
        self._validate_entry(entry)
        return entry

    # ---------- 内置条目的停用偏好（只写用户侧文件，内置文件一字未改）----------

    def disabled_builtin_ids(self) -> set[str]:
        """我停用过的内置条目 id（文件缺失或损坏时视为空集）。"""
        path = self._disabled_path()
        if not path.is_file():
            return set()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 停用偏好坏掉只应让内置条目回到「启用」，不该阻塞任何接口
            logger.warning("内置条目停用清单读取失败，按未停用处理：%s", path)
            return set()
        ids = raw.get("ids") if isinstance(raw, dict) else None
        return {str(item) for item in ids} if isinstance(ids, list) else set()

    def set_builtin_entry_enabled(self, entry_id: str, enabled: bool) -> set[str]:
        """停用 / 启用一个**内置**条目，返回更新后的停用集合。"""
        builtin_ids = {entry.id for entry in self._builtin_entries()}
        if entry_id not in builtin_ids:
            raise StudioError(
                f"{entry_id} 不是内置条目；我的条目请用「编辑」直接改启用状态"
            )
        disabled = self.disabled_builtin_ids()
        if enabled:
            disabled.discard(entry_id)
        else:
            disabled.add(entry_id)
        if disabled:
            self._write_json(self._disabled_path(), {"ids": sorted(disabled)})
        else:
            # 全部恢复启用时删掉文件，避免留下一个空清单造成误解
            self._disabled_path().unlink(missing_ok=True)
        return disabled

    # ---------- 文风预设：详情与写入 ----------

    def get_style(self, style_id: str) -> tuple[StylePreset, bool]:
        """取单个文风预设 → (预设, 是否内置)；不存在抛 FileNotFoundError。"""
        self._validate_id(style_id, label="文风")
        for preset, builtin in self._merged_styles()[0]:
            if preset.id == style_id:
                return preset, builtin
        raise FileNotFoundError(f"文风预设不存在：{style_id}")

    def create_style(
        self,
        *,
        name: str,
        style_prompt: str,
        description: str = "",
        tags: list[str] | None = None,
        avoid: list[str] | None = None,
        examples: list[dict] | None = None,
        sampling: dict[str, float] | None = None,
        conflicts_with: list[str] | None = None,
    ) -> StylePreset:
        """创建我的文风预设（id 自动生成，`user-` 前缀）。

        与角色不同，文风 id **不承载任何持久语义**（记忆隔离用的是人设 id），
        所以这里允许后续「复制」而不必担心换记忆——但仍然不允许改 id，
        否则文件名与内容里的 id 会分叉。
        """
        preset = StylePreset(
            id=self._allocate_id(_slugify(name)),
            name=_require_text(name, "文风名称", max_chars=_MAX_STYLE_NAME),
            description=_optional_text(
                description, "文风说明", max_chars=_MAX_STYLE_DESCRIPTION
            ),
            tags=_clean_list(
                tags, label="标签", max_items=_MAX_TAG_ITEMS, max_chars=_MAX_TAG_CHARS
            ),
            style_prompt=_require_text(
                style_prompt, "风格指令", max_chars=_MAX_STYLE_PROMPT
            ),
            avoid=_clean_list(
                avoid,
                label="避免表达",
                max_items=_MAX_STYLE_AVOID_ITEMS,
                max_chars=_MAX_STYLE_AVOID_CHARS,
            ),
            examples=_clean_examples(examples),
            sampling=_clean_sampling(sampling),
            conflicts_with=_clean_list(
                conflicts_with,
                label="冲突特质",
                max_items=_MAX_STYLE_CONFLICT_ITEMS,
                max_chars=_MAX_STYLE_CONFLICT_CHARS,
            ),
        )
        return self._write_style(preset)

    def update_style(
        self,
        style_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        style_prompt: str | None = None,
        tags: list[str] | None = None,
        avoid: list[str] | None = None,
        examples: list[dict] | None = None,
        sampling: dict[str, float] | None = None,
        conflicts_with: list[str] | None = None,
    ) -> StylePreset:
        """更新我的文风预设（内置 → BuiltinReadOnlyError）。

        `None` = 不修改该字段；要清空请传空列表 / 空 dict（`name` 与
        `style_prompt` 不接受清空——没有风格指令的文风预设等于没写）。
        """
        current, builtin = self.get_style(style_id)
        if builtin:
            raise BuiltinReadOnlyError(
                f"「{current.name}」是内置文风，不能直接修改；"
                "请用「复制为我的文风」得到一份可编辑的副本"
            )
        changes: dict[str, object] = {}
        if name is not None:
            changes["name"] = _require_text(name, "文风名称", max_chars=_MAX_STYLE_NAME)
        if description is not None:
            changes["description"] = _optional_text(
                description, "文风说明", max_chars=_MAX_STYLE_DESCRIPTION
            )
        if style_prompt is not None:
            changes["style_prompt"] = _require_text(
                style_prompt, "风格指令", max_chars=_MAX_STYLE_PROMPT
            )
        if tags is not None:
            changes["tags"] = _clean_list(
                tags, label="标签", max_items=_MAX_TAG_ITEMS, max_chars=_MAX_TAG_CHARS
            )
        if avoid is not None:
            changes["avoid"] = _clean_list(
                avoid,
                label="避免表达",
                max_items=_MAX_STYLE_AVOID_ITEMS,
                max_chars=_MAX_STYLE_AVOID_CHARS,
            )
        if examples is not None:
            changes["examples"] = _clean_examples(examples)
        if sampling is not None:
            changes["sampling"] = _clean_sampling(sampling)
        if conflicts_with is not None:
            changes["conflicts_with"] = _clean_list(
                conflicts_with,
                label="冲突特质",
                max_items=_MAX_STYLE_CONFLICT_ITEMS,
                max_chars=_MAX_STYLE_CONFLICT_CHARS,
            )

        # 走 model_validate 而不是 model_copy：后者绕过 pydantic 校验
        updated = StylePreset.model_validate({**current.model_dump(), **changes})
        return self._write_style(updated)

    def duplicate_style(self, style_id: str, *, name: str | None = None) -> StylePreset:
        """复制一份文风预设（内置文风借此变成可编辑的「我的文风」）。"""
        source, _ = self.get_style(style_id)
        return self.create_style(
            name=(name.strip() if name else f"{source.name}（副本）"),
            description=source.description,
            style_prompt=source.style_prompt,
            tags=list(source.tags),
            avoid=list(source.avoid),
            examples=[
                {"user": example.user, "assistant": example.assistant}
                for example in source.examples
            ],
            sampling=dict(source.sampling),
            conflicts_with=list(source.conflicts_with),
        )

    def delete_style(self, style_id: str) -> StylePreset:
        """从当前工坊删除文风预设。

        用户预设删 YAML；内置预设写入用户侧隐藏清单，不修改包内资源。
        与角色不同，**没有级联**：文风不承载会话与记忆，偏好里残留一个已删除的
        style_id 只会让那一轮回落默认（`_resolve_style` 会给出告警）。
        """
        preset, builtin = self.get_style(style_id)
        if builtin:
            self._delete_builtin_style(style_id)
        else:
            self._style_path(style_id).unlink(missing_ok=True)
        return preset

    # ---------- 内部：校验、id 分配、落盘 ----------

    def _validate_scope(self, scope: str) -> str:
        """校验条目归属：`*` 或一个**确实存在**的角色 id。"""
        text = (scope or "").strip()
        if text == SCOPE_ALL:
            return text
        if not _ID_PATTERN.match(text):
            raise StudioError(
                f"非法的归属取值：{scope!r}（只允许 `*` 或角色 id）"
            )
        personas, _ = self.all_personas()
        if text not in personas:
            raise StudioError(
                f"归属角色不存在：{text}——请先创建该角色，"
                "或用「所有角色」表示对每个陪伴对象都生效"
            )
        return text

    @staticmethod
    def _validate_entry(entry: WorldBookEntry) -> None:
        """条目自检：必须至少有一个触发条件，且正则必须可编译。

        正则为什么必须在这里就拦住：`matcher` 是直接 `re.search` 的，
        用户写错一个括号会让**每一轮对话**都在召回阶段抛异常，
        而不是只让这一条不生效——早失败远好过线上 500。
        """
        if not entry.has_trigger:
            raise StudioError(
                f"条目「{entry.title}」缺少触发条件："
                "关键词 / 正则 / 语义触发文本至少填一项"
            )
        for pattern in entry.regex:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise StudioError(
                    f"条目「{entry.title}」的正则非法：{pattern!r}（{exc}）"
                ) from exc

    def _allocate_id(self, slug: str) -> str:
        """分配 id：`user-<slug>`；slug 为空（中文标题）或已被占用时回退随机后缀。"""
        taken = self._taken_ids()
        candidate = f"{_ID_PREFIX}{slug}" if slug else ""
        if candidate and _ID_PATTERN.match(candidate) and candidate not in taken:
            return candidate
        for _ in range(64):
            candidate = f"{_ID_PREFIX}{secrets.token_hex(3)}"
            if candidate not in taken:
                return candidate
        raise StudioError("无法分配唯一 id：本地用户内容过多，请先清理")

    def _taken_ids(self) -> set[str]:
        personas, _ = self.all_personas()
        entries, _ = self.all_entries()
        styles, _ = self.all_styles()
        return set(personas) | {entry.id for entry in entries} | set(styles)

    def _write_persona(self, preset: PersonaPreset) -> PersonaPreset:
        """落盘并**重读**返回：等于顺带做了一次 round-trip 自检。"""
        path = self._persona_path(preset.id)
        self._write_yaml(path, _persona_payload(preset))
        return load_preset_file(path)

    def _write_entry(self, entry: WorldBookEntry) -> WorldBookEntry:
        """落盘并**重读**返回（同上，保证写进去的能读出来）。"""
        path = self._entry_path(entry.id)
        self._write_yaml(path, _entry_payload(entry))
        return load_entry_file(path)

    def _write_style(self, preset: StylePreset) -> StylePreset:
        """落盘并**重读**返回（同上）。"""
        path = self._style_path(preset.id)
        self._write_yaml(path, _style_payload(preset))
        return load_style_file(path)

    @staticmethod
    def _write_yaml(path: Path, payload: dict) -> None:
        """原子写 YAML：先写临时文件再替换，中途中断不会留下半个文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        text = yaml.dump(
            payload,
            Dumper=_BlockStyleDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
