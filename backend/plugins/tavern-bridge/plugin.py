"""酒馆数据接入插件（只读）—— 插件体系的第一个 `datasource` 实现。

定位（`AGENTS.md §8` / `§9.8`）：HyPRA **不做** SillyTavern 客户端，而是作为
伴随 Agent **只读接入**用户的酒馆数据：

    世界书 worlds/*.json      → 中性条目 → 宿主映射为 WorldBookEntry → 提示词注入
    角色卡 characters/*.png   → 中性角色 → 宿主映射为 persona
    会话   chats/**/*.jsonl   → 中性会话 → 宿主抽取为跨会话长期记忆

**零风险承诺**：manifest 声明 `filesystem.write = false`，宿主因此**不会**授予写句柄；
即使本文件尝试写入，`ctx.write_text()` 也会抛 `PermissionError`。这不是约定，是架构约束。

实现约束（均来自对真实酒馆数据的勘察，见 `AGENTS.md §8.4`）：

1. 角色卡是 PNG `tEXt` chunk，键为 `chara`(V2) 与 `ccv3`(V3)，
   **两个值都是 base64 编码的 JSON**；解析时 `ccv3` 优先、`chara` 回退。
2. **V1 字段回填是必需的**：实测 `data.description` 常为空而正文在**顶层** `description`。
3. 真实世界书以「常驻 + 深度注入」为主（`constant=True` / `position=4` / `depth=4`），
   而不是关键词触发——因此中性契约把 `constant` 与 `position`/`depth` 作为一等字段。
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.plugins.contracts import (
    DataSourceCharacter,
    DataSourceEntry,
    DataSourceMessage,
    DataSourceSession,
    DataSourceSnapshot,
)

if TYPE_CHECKING:  # 仅供类型检查：运行时不导入宿主实现，避免插件与宿主强耦合
    from app.plugins.context import PluginContext

PLUGIN_ID = "tavern-bridge"

#: ST 世界书 `position` 数值 → 中性档位字符串（0/1 在人设前后，2/3 在作者注前后，4 按深度插入）
_POSITION_MAP: dict[int, str] = {
    0: "before_char",
    1: "after_char",
    2: "before_an",
    3: "after_an",
    4: "at_depth",
}

#: PNG 文件签名
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: zTXt 解压上限（防解压炸弹）
_MAX_ZTXT_BYTES = 50 * 1024 * 1024

#: V1 顶层字段 —— V3 的 `data` 里为空时必须从顶层回填
_V1_FALLBACK_FIELDS = (
    "name",
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "creatorcomment",
    "avatar",
    "character_version",
    "creator",
    "creator_notes",
    "tags",
    "alternate_greetings",
)

#: ST 世界书条目里已知字段（其余一律进 `extra` 透传）
_KNOWN_ENTRY_FIELDS = frozenset(
    {
        "comment",
        "content",
        "key",
        "constant",
        "position",
        "depth",
        "order",
        "disable",
        "caseSensitive",
        "probability",
        "uid",
    }
)


# =============================================================
# PNG 角色卡解析
# =============================================================


def _decompress_ztxt(payload: bytes) -> bytes:
    """解压 zTXt 载荷并限制输出大小（防解压炸弹）。"""
    decompressor = zlib.decompressobj()
    out = decompressor.decompress(payload, _MAX_ZTXT_BYTES + 1)
    if len(out) > _MAX_ZTXT_BYTES:
        raise ValueError("zTXt 解压超过上限（疑似解压炸弹）")
    return out


def png_text_chunks(raw: bytes) -> dict[str, str]:
    """提取 PNG 的 tEXt / zTXt 文本块（键 → 原值）。"""
    if not raw.startswith(_PNG_SIGNATURE):
        raise ValueError("不是 PNG 文件（签名不匹配）")

    chunks: dict[str, str] = {}
    offset = len(_PNG_SIGNATURE)
    total = len(raw)
    while offset + 12 <= total:
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        chunk_type = raw[offset + 4 : offset + 8]
        data = raw[offset + 8 : offset + 8 + length]
        offset += 12 + length  # 长度(4) + 类型(4) + 数据 + CRC(4)

        if chunk_type == b"tEXt":
            key, _, value = data.partition(b"\x00")
            chunks[key.decode("latin-1")] = value.decode("latin-1")
        elif chunk_type == b"zTXt":
            key, _, rest = data.partition(b"\x00")
            if rest[:1] == b"\x00":  # 压缩方法 0 = zlib
                chunks[key.decode("latin-1")] = _decompress_ztxt(rest[1:]).decode("latin-1")
        elif chunk_type == b"IEND":
            break
    return chunks


def merge_v1_fields(card: dict[str, Any]) -> dict[str, Any]:
    """把顶层 V1 字段回填进 `data`（仅在 `data` 缺失或为空时），返回合并后的 `data`。

    实测 V3 卡的 `data.description` 常为空、正文写在顶层——不回填会读到空设定。
    """
    data = dict(card.get("data") or {})
    for field_name in _V1_FALLBACK_FIELDS:
        top_value = card.get(field_name)
        if top_value in (None, "", [], {}):
            continue
        if data.get(field_name) in (None, "", [], {}):
            data[field_name] = top_value
    return data


def read_character_card(raw: bytes) -> dict[str, Any] | None:
    """解析 PNG 角色卡，返回**合并后**的 `data`；没有内嵌数据时返回 None。"""
    chunks = png_text_chunks(raw)
    payload = chunks.get("ccv3") or chunks.get("chara")
    if not payload:
        return None
    # 两个 chunk 的值都是 base64 编码的 JSON（勘察确认，见 AGENTS.md §8.4）
    decoded = json.loads(base64.b64decode(payload).decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("角色卡内嵌数据不是 JSON 对象")
    return merge_v1_fields(decoded)


# =============================================================
# 世界书 / 条目
# =============================================================


def _as_str_list(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, list):
        return tuple(str(item) for item in value if str(item).strip())
    return ()


def entries_from_book(data: Any, *, source_id: str) -> list[DataSourceEntry]:
    """把一本世界书（`worlds/*.json` 或角色卡内嵌 `character_book`）转成中性条目。

    兼容三种 `entries` 形态：字典（ST 用 uid 作键）、列表（部分导出工具）、缺失。
    """
    if not isinstance(data, dict):
        return []
    raw_entries = data.get("entries")
    if isinstance(raw_entries, dict):
        items = list(raw_entries.values())
    elif isinstance(raw_entries, list):
        items = raw_entries
    else:
        return []

    out: list[DataSourceEntry] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue

        keys = _as_str_list(raw.get("key"))
        title = str(raw.get("comment") or "").strip()
        if not title:
            title = keys[0] if keys else f"条目 {index + 1}"

        try:
            position_value = int(raw.get("position", 1))
        except (TypeError, ValueError):
            position_value = 1

        try:
            probability = int(raw.get("probability", 100))
        except (TypeError, ValueError):
            probability = 100

        out.append(
            DataSourceEntry(
                title=title,
                content=content,
                keys=keys,
                constant=bool(raw.get("constant", False)),
                position=_POSITION_MAP.get(position_value, "after_char"),
                depth=int(raw.get("depth") or 4),
                order=int(raw.get("order") or 100),
                enabled=not bool(raw.get("disable", False)),
                case_sensitive=bool(raw.get("caseSensitive", False)),
                probability=max(0, min(100, probability)),
                source=PLUGIN_ID,
                source_id=f"{source_id}#{raw.get('uid', index)}",
                extra={
                    key: value
                    for key, value in raw.items()
                    if key not in _KNOWN_ENTRY_FIELDS
                },
            )
        )
    return out


def character_from_card(
    data: dict[str, Any], *, avatar_path: str, source_id: str
) -> DataSourceCharacter:
    """把合并后的角色卡 `data` 转成中性角色。"""
    greetings = _as_str_list(data.get("alternate_greetings"))
    return DataSourceCharacter(
        name=str(data.get("name") or "").strip() or Path(avatar_path).stem,
        description=str(data.get("description") or ""),
        personality=str(data.get("personality") or ""),
        scenario=str(data.get("scenario") or ""),
        first_message=str(data.get("first_mes") or ""),
        alternate_greetings=greetings,
        example_messages=str(data.get("mes_example") or ""),
        system_prompt=str(data.get("system_prompt") or ""),
        post_history_instructions=str(data.get("post_history_instructions") or ""),
        avatar_path=avatar_path,
        source=PLUGIN_ID,
        source_id=source_id,
        extra={
            "creator": data.get("creator") or data.get("creatorcomment") or "",
            "creator_notes": data.get("creator_notes") or "",
            "character_version": data.get("character_version") or "",
            "tags": list(_as_str_list(data.get("tags"))),
        },
    )


# =============================================================
# 数据源
# =============================================================


class TavernDataSource:
    """酒馆用户数据目录的只读访问器。

    数据访问一律走 `PluginContext` 的受控 API（`list_dir` / `read_json` / `read_bytes`），
    因此**路径白名单之外的文件既看不到也读不到**——权限在宿主侧强制，插件无从绕过。
    """

    #: 用户可能填的三种目录层级（ST 根目录 / data 目录 / profile 目录）
    _ROOT_PROBES = ("", "data/default-user", "default-user")

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.root = self._detect_root()

    # ---- 目录定位 ----

    def _detect_root(self) -> Path | None:
        raw = str(self.ctx.settings.get("tavern_dir") or "").strip()
        if not raw:
            return None
        base = Path(raw)
        for probe in self._ROOT_PROBES:
            candidate = base / probe if probe else base
            if not candidate.is_dir():
                continue
            if (candidate / "worlds").is_dir() or (candidate / "characters").is_dir():
                return candidate
        return None

    # ---- 读取 ----

    def read(self) -> DataSourceSnapshot:
        """读一遍全部数据；单个文件损坏只记 warning，不中断整体接入。"""
        snapshot = DataSourceSnapshot()
        if self.root is None:
            snapshot.warnings.append(
                "未配置有效的酒馆数据目录（需指向含 worlds/ 或 characters/ 的目录）"
            )
            return snapshot

        self._read_worlds(snapshot)
        self._read_characters(snapshot)
        self._read_sessions(snapshot)
        return snapshot

    def _iter_dir(self, directory: Path, snapshot: DataSourceSnapshot) -> list[Path]:
        """列目录；越权或缺失都只记 warning。"""
        if not directory.is_dir():
            return []
        try:
            return sorted(self.ctx.list_dir(directory))
        except PermissionError as exc:
            snapshot.warnings.append(f"目录不在插件读权限内：{directory.name}（{exc}）")
            return []

    def _read_worlds(self, snapshot: DataSourceSnapshot) -> None:
        assert self.root is not None  # noqa: S101 - read() 已保证
        for path in self._iter_dir(self.root / "worlds", snapshot):
            if path.suffix.lower() != ".json":
                continue
            try:
                data = self.ctx.read_json(path)
            except Exception as exc:
                snapshot.warnings.append(f"世界书读取失败：{path.name}（{exc}）")
                continue
            entries = entries_from_book(data, source_id=f"world/{path.stem}")
            if not entries:
                snapshot.warnings.append(f"世界书没有可用条目：{path.name}")
            snapshot.entries.extend(entries)

    def _read_characters(self, snapshot: DataSourceSnapshot) -> None:
        assert self.root is not None  # noqa: S101
        for path in self._iter_dir(self.root / "characters", snapshot):
            if path.suffix.lower() != ".png":
                continue
            try:
                data = read_character_card(self.ctx.read_bytes(path))
            except Exception as exc:
                snapshot.warnings.append(f"角色卡解析失败：{path.name}（{exc}）")
                continue
            if data is None:
                snapshot.warnings.append(f"角色卡没有内嵌数据（跳过）：{path.name}")
                continue

            character = character_from_card(data, avatar_path=str(path), source_id=path.stem)
            snapshot.characters.append(character)

            # 角色内嵌世界书同样产出为条目，并标注归属角色（宿主据此设 scope）
            book_entries = entries_from_book(
                data.get("character_book"), source_id=f"char/{path.stem}"
            )
            for entry in book_entries:
                entry.extra.setdefault("character", character.name)
            snapshot.entries.extend(book_entries)

    def _read_sessions(self, snapshot: DataSourceSnapshot) -> None:
        assert self.root is not None  # noqa: S101
        chats_root = self.root / "chats"
        for character_dir in self._iter_dir(chats_root, snapshot):
            if not character_dir.is_dir():
                continue
            for path in self._iter_dir(character_dir, snapshot):
                if path.suffix.lower() != ".jsonl":
                    continue
                session = self._read_session(path, character_name=character_dir.name, snapshot=snapshot)
                if session is not None and session.messages:
                    snapshot.sessions.append(session)

    def _read_session(
        self, path: Path, *, character_name: str, snapshot: DataSourceSnapshot
    ) -> DataSourceSession | None:
        try:
            raw_text = self.ctx.read_text(path)
        except Exception as exc:
            snapshot.warnings.append(f"会话读取失败：{path.name}（{exc}）")
            return None

        messages: list[DataSourceMessage] = []
        metadata: dict[str, Any] = {}
        for line_number, line in enumerate(raw_text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                snapshot.warnings.append(f"会话行损坏：{path.name} 第 {line_number} 行")
                continue
            if not isinstance(row, dict):
                continue
            # 首行的 chat_metadata 是会话元信息，不是消息
            if line_number == 1 and isinstance(row.get("chat_metadata"), dict):
                metadata = row["chat_metadata"]
                continue
            text = str(row.get("mes") or "").strip()
            if not text:
                continue
            messages.append(
                DataSourceMessage(
                    role="user" if row.get("is_user") else "assistant",
                    text=text,
                    created_at=str(row.get("send_date") or ""),
                    name=str(row.get("name") or ""),
                )
            )

        return DataSourceSession(
            id=path.stem,
            character_name=character_name,
            title=path.stem,
            created_at=str(metadata.get("create_date") or ""),
            messages=tuple(messages),
            source=PLUGIN_ID,
            extra={"path": str(path)},
        )

    def describe(self) -> str:
        """人类可读的接入状态（供管理 UI 与工具返回值）。"""
        if self.root is None:
            return "未配置酒馆数据目录"
        return f"已接入 {self.root}"


# =============================================================
# 工具
# =============================================================


def build_status_tool(snapshot: DataSourceSnapshot) -> Any:
    """构造 `tavern_library_status` 工具：让模型知道能看到哪些酒馆资料。"""
    from pydantic import BaseModel

    from app.tools.registry import ToolSpec

    class NoArgs(BaseModel):
        """无参数。"""

    def handler(args: Any, context: Any) -> dict:  # noqa: ARG001 - 保持 ToolSpec 签名
        return {
            "entries": len(snapshot.entries),
            "characters": [character.name for character in snapshot.characters],
            "sessions": len(snapshot.sessions),
            "sessions_in_chars": sum(len(s.messages) for s in snapshot.sessions),
            "warnings": snapshot.warnings[:5],
        }

    return ToolSpec(
        name="tavern_library_status",
        description=(
            "查看已接入的酒馆资料库：世界书条目数、可用角色、会话数量。"
            "当用户问「你了解我的设定吗」「你看得到我的酒馆吗」，或你需要确认"
            "自己掌握哪些设定时使用。"
        ),
        parameters=NoArgs.model_json_schema(),
        args_model=NoArgs,
        handler=handler,
        tags=["tavern", "memory"],
    )


# =============================================================
# 插件入口
# =============================================================


def build(ctx: PluginContext) -> dict[str, Any]:
    """插件入口（宿主在 setup 阶段调用）：返回本插件对宿主的贡献。"""
    source = TavernDataSource(ctx)
    snapshot = source.read()

    ctx.logger.info(
        "酒馆数据接入：%s（条目 %d / 角色 %d / 会话 %d）",
        source.describe(),
        len(snapshot.entries),
        len(snapshot.characters),
        len(snapshot.sessions),
    )
    for warning in snapshot.warnings[:10]:
        ctx.logger.warning("酒馆数据接入警告：%s", warning)

    return {
        "datasources": [source],
        "tools": [build_status_tool(snapshot)],
    }
