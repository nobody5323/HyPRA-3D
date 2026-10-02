"""酒馆会话 → 长期记忆导入器（宿主侧映射层）。

设计依据 `AGENTS.md §9.4` 的硬约定：插件只产出**中性数据**（`DataSourceSession`），
「映射到宿主模型」由宿主完成——本模块就是那个映射层。

用途：兑现 `AGENTS.md §8.2` 的「桌宠跨会话长期记忆」——把用户在酒馆里的历史对话，
经 `MemoryStore.remember_turn` 转为 HyPRA 的**情景记忆**（温层向量）与**语义事实**（冷层），
使陪伴对象「记得」认识用户之前发生过什么。

两个要点：
- **增量**：已导入到哪一轮记在 `data/tavern_import.json`（**轮次级游标**），
  再导入只处理新增部分——重复点不会写重，同一段剧情接着聊也能补上；
- **不阻断**：单轮写入失败只记 warning，其余照常导入——不能因为一条坏数据放弃整批记忆。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.plugins.contracts import DataSourceMessage, DataSourceSession

logger = logging.getLogger(__name__)

#: 已导入进度的记录文件（相对 backend 运行目录；data/ 已被 .gitignore 覆盖）
DEFAULT_STATE_FILE = "data/tavern_import.json"

#: 一个陪伴对象下「会话 id → 已导入的已完成轮次数」
SessionCursors = dict[str, int]

#: 旧格式遗留（只有会话粒度、不知道导到第几轮）的游标值：表示「游标未知」
CURSOR_UNKNOWN = -1


@dataclass
class ImportResult:
    """一次导入的统计（供 API 与 UI 展示）。"""

    sessions_total: int = 0
    sessions_imported: int = 0
    sessions_skipped: int = 0
    turns: int = 0
    facts: int = 0
    memories: int = 0
    #: 每个陪伴对象写入了多少轮（一对多：酒馆里不同角色进不同的记忆）
    scopes: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sessions_total": self.sessions_total,
            "sessions_imported": self.sessions_imported,
            "sessions_skipped": self.sessions_skipped,
            "turns": self.turns,
            "facts": self.facts,
            "memories": self.memories,
            "scopes": self.scopes,
            "warnings": self.warnings,
        }


def pair_turns(messages: Sequence[DataSourceMessage]) -> list[tuple[str, str]]:
    """把消息序列配成 `(user_text, assistant_text)` 轮次。

    酒馆的 jsonl 通常 user/assistant 交替，但不保证：可能有连续同角色消息、
    系统消息（本函数忽略）、或以 assistant 开头。配对策略：

    - `user` 消息累积到待配对缓冲（连续多条会拼成一句）；
    - 遇到 `assistant` 就与缓冲配对并清空；
    - 结尾若仍有未配对的 user，与空回复配对（保留用户的表达）。
    """

    turns: list[tuple[str, str]] = []
    pending: list[str] = []

    for message in messages:
        role = (message.role or "").strip().lower()
        text = (message.text or "").strip()
        if not text:
            continue
        if role == "user":
            pending.append(text)
        elif role == "assistant":
            turns.append((" ".join(pending), text))
            pending = []
        # 其它角色（system 等）不进对话轮次

    if pending:
        turns.append((" ".join(pending), ""))
    return turns


def completed_turns(messages: Sequence[DataSourceMessage]) -> list[tuple[str, str]]:
    """配好轮次并去掉结尾那个**悬空轮**（返回值里每一项的助手都有正文）。

    为什么要分出来：`pair_turns` 把结尾未配对的 user 补成 `(用户那句, "")`，
    那是刻意的（保留用户的表达），但它**不能进导入游标**——等酒馆里助手的
    回复来了，这一轮的配对会变成 `(用户那句, 助手回复)`；游标若已把它算作
    「导过」，助手那句就**永久丢失**。

    所以游标只数「助手有正文」的轮次，悬空那一轮不导入也不推进游标，
    等回复到了再一起进。代价是用户最后一句在回复前不在记忆里——下一轮补上。

    注：悬空轮最多只会有一个且在末尾（空正文的 assistant 消息在 `pair_turns`
    里会被跳过）——所以这里只需看最后一项。
    """
    turns = pair_turns(messages)
    if turns and not turns[-1][1].strip():
        return turns[:-1]
    return turns


def _start_index(cursor: int, total: int) -> int:
    """增量边界（纯计算，不发警告）——`import_sessions` 与 `pending_progress` 共用。

    两处共用是**硬要求**：界面显示「待同步 N 轮」与实际点下去写入的轮次
    必须一致，各算一套的话用户会看到「说有 3 轮，点了却 0 轮」。

    - `CURSOR_UNKNOWN`（旧格式遗留）→ 从**末尾**开始（按当前轮数建基线）；
    - 游标比现有轮数还大（会话被编辑 / 删短了）→ 钳到末尾，**不倒退重导**
      （重导会往向量库与事实表写重复项，比少导几轮更脏）。
    """
    if cursor == CURSOR_UNKNOWN:
        return total
    return max(0, min(cursor, total))


def _resolve_start(
    cursor: int, total: int, session: DataSourceSession, result: ImportResult
) -> int:
    """`_start_index` 加一层告警（导入路径才需要告诉用户“会话变短了”）。"""
    if cursor != CURSOR_UNKNOWN and cursor > total:
        result.warnings.append(
            f"会话比上次导入时更短了（{cursor} → {total} 轮，可能被编辑或删除），"
            f"已按当前轮数重设进度：{session.title or session.id}"
        )
    return _start_index(cursor, total)


def _resolve_scope(
    session: DataSourceSession,
    character_scopes: Mapping[str, str] | None,
    fallback: str,
) -> str:
    """这条会话该写进哪个陪伴对象的记忆。

    按**角色**分开是刻意的（`AGENTS.md §8.2` 的记忆隔离）：酒馆里几个角色的剧情
    全灌进同一个陪伴对象后，选谁都会召回别人的剧情——与刚修好的世界书
    `scope` 隔离是同一个道理。

    匹配不到角色卡的会话（卡被删了）回落到调用方给的 `companion_id`：
    丢数据比串味更糟，而且这种情况会在警告里说清数量。
    """
    if character_scopes:
        scope = character_scopes.get(session.character_name)
        if scope:
            return scope
    return fallback


class TavernMemoryImporter:
    """把酒馆会话导入为 HyPRA 的长期记忆。"""

    def __init__(self, memory_store, *, state_path: str | Path = DEFAULT_STATE_FILE) -> None:
        self.memory_store = memory_store
        self.state_path = Path(state_path)

    # ---------------- 已导入进度（增量基础）----------------

    def _load_state(self) -> dict[str, SessionCursors]:
        """读进度：`{companion_id: {session_id: 已完成轮次数}}`。

        兼容旧格式（`{companion_id: [session_id, ...]}`，只有会话粒度）：
        旧值转成 `{"<会话>": CURSOR_UNKNOWN}`。

        **游标未知时代价最小的一边是什么**：不重复导入——把已经导过的几百轮
        再写一遍会往向量库里塞重复项（召回时占位、事实表也会灍），而旧格式下
        新增的轮次本来就导不进来（这是旧实现的确定行为），所以迁移不会让
        任何东西变差。下次导入时按**当前**轮次数建立基线，从此增量生效。
        """
        if not self.state_path.is_file():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # 状态文件损坏：当作没导入过，不阻断
            logger.warning("酒馆导入状态读取失败（将以空记录继续）：%s", exc)
            return {}
        if not isinstance(data, dict):
            return {}

        state: dict[str, SessionCursors] = {}
        for key, value in data.items():
            if isinstance(value, dict):  # 新格式
                state[str(key)] = {
                    str(sid): int(cursor)
                    for sid, cursor in value.items()
                    if isinstance(cursor, int)
                }
            elif isinstance(value, list):  # 旧格式：只有会话粒度
                state[str(key)] = {
                    str(item): CURSOR_UNKNOWN for item in value
                }
        return state

    def _save_state(self, state: dict[str, SessionCursors]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def imported_sessions(self, companion_id: str) -> list[str]:
        """某陪伴对象已导入过的会话 id（UI 用于显示"已导入 N 个会话"）。"""
        return sorted(self._load_state().get(companion_id, {}))

    def imported_turns(self, companion_id: str, session_id: str) -> int:
        """某会话已导入到第几轮（`CURSOR_UNKNOWN` 表示旧格式遗留、游标未知）。"""
        return self._load_state().get(companion_id, {}).get(session_id, 0)

    def pending_progress(
        self,
        sessions: Iterable[DataSourceSession],
        *,
        companion_id: str,
        character_scopes: Mapping[str, str] | None = None,
    ) -> tuple[int, int]:
        """待同步统计 → `(新增轮次数, 涉及的会话数)`。**不写任何记忆**。

        规则与 `import_sessions` 完全共用（`completed_turns` + `_start_index`），
        所以界面上的数字就是点一下会发生的写入量。
        """
        state = self._load_state()
        pending_turns = 0
        pending_sessions = 0
        for session in sessions:
            turns = completed_turns(session.messages)
            if not turns:
                continue
            scope = _resolve_scope(session, character_scopes, companion_id)
            cursor = state.get(scope, {}).get(session.id, 0)
            missing = len(turns) - _start_index(cursor, len(turns))
            if missing <= 0:
                continue
            pending_turns += missing
            pending_sessions += 1
        return pending_turns, pending_sessions

    def reset(self, companion_id: str = "") -> None:
        """清除导入进度（不删记忆本身）。传空串清空全部。"""
        if not companion_id:
            self._save_state({})
            return
        state = self._load_state()
        state.pop(companion_id, None)
        self._save_state(state)

    # ---------------- 导入 ----------------

    def import_sessions(
        self,
        sessions: Iterable[DataSourceSession],
        *,
        companion_id: str,
        character_scopes: Mapping[str, str] | None = None,
        force: bool = False,
        source: str = "tavern",
    ) -> ImportResult:
        """把会话导入为记忆（**增量**：只写上次之后新增的轮次）。

        每个会话按角色落到**各自**的陪伴对象（`character_scopes`），不是全灌一个。

        参数:
            companion_id: **回落**作用域——匹配不到角色卡的会话写到这里（`AGENTS.md §8.2`）；
            character_scopes: 角色名 → 陪伴对象 id；酒馆里不同角色因此各进各的记忆。
                由调用方从数据源快照算（`app.prompts.persona.datasource_map
                .scopes_by_character_name`），保证与世界书的 `scope` 用同一个 id 函数。
                不给就全部落到 `companion_id`（单角色场景的旧行为）；
            force: 为 True 时忽略进度记录，从**头**重导（用于修复或换 embedding 后重建）。
                它会把你已导过的轮次再写一遍——界面必须二次确认。
        """
        if not companion_id:
            raise ValueError("导入记忆必须指定 companion_id（记忆作用域不可为空）")

        session_list = list(sessions)
        result = ImportResult(sessions_total=len(session_list))

        state = self._load_state()
        # 一次调用可能写进多个陪伴对象 → 每个 scope 各有一份进度表
        cursors_by_scope: dict[str, SessionCursors] = {}
        legacy = 0
        unmatched = 0

        for session in session_list:
            # 只算「已完成」的轮次：悬空轮不进游标（见 completed_turns）
            turns = completed_turns(session.messages)
            if not turns:
                result.sessions_skipped += 1
                result.warnings.append(
                    f"会话没有可导入的完整轮次（已跳过）：{session.title or session.id}"
                )
                continue

            scope = _resolve_scope(session, character_scopes, companion_id)
            if character_scopes and scope == companion_id:
                unmatched += 1
            cursors = cursors_by_scope.setdefault(scope, dict(state.get(scope, {})))

            cursor = cursors.get(session.id, 0)
            start = 0 if force else _resolve_start(cursor, len(turns), session, result)
            if not force and cursor == CURSOR_UNKNOWN:
                legacy += 1

            if start >= len(turns):  # 没有新轮次
                result.sessions_skipped += 1
                cursors[session.id] = len(turns)
                continue

            for turn_index in range(start, len(turns)):
                user_text, assistant_text = turns[turn_index]
                try:
                    stats = self.memory_store.remember_turn(
                        scope,
                        user_text,
                        assistant_text,
                        turn_index=turn_index,
                        source=f"{source}:{session.id}",
                    )
                except Exception as exc:  # 单轮失败不放弃整个会话
                    result.warnings.append(
                        f"写入失败：{session.title or session.id} 第 {turn_index + 1} 轮（{exc}）"
                    )
                    continue
                result.turns += 1
                result.facts += int(stats.get("facts", 0))
                result.memories += int(stats.get("memory", 0))
                result.scopes[scope] = result.scopes.get(scope, 0) + 1

            result.sessions_imported += 1
            cursors[session.id] = len(turns)

        if legacy:
            result.warnings.append(
                f"{legacy} 个会话的导入记录是旧格式（只记到会话、不记到轮次），"
                "已按当前轮数建立基线：从此刻起新增对话会自动补上；"
                "若想把旧记录之后漏掉的内容也补回来，用「重新导入」（会重复写入已导过的轮次）"
            )
        if unmatched:
            result.warnings.append(
                f"{unmatched} 个会话找不到对应角色卡，已归到当前陪伴对象"
            )

        state.update(cursors_by_scope)
        self._save_state(state)

        logger.info(
            "酒馆记忆导入完成：会话 %d/%d（跳过 %d），新增轮次 %d，事实 %d，情景 %d，写入 %d 个记忆作用域",
            result.sessions_imported,
            result.sessions_total,
            result.sessions_skipped,
            result.turns,
            result.facts,
            result.memories,
            len(result.scopes),
        )
        return result
