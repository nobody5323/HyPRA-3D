"""酒馆会话 → 长期记忆导入器（宿主侧映射层）。

设计依据 `AGENTS.md §9.4` 的硬约定：插件只产出**中性数据**（`DataSourceSession`），
「映射到宿主模型」由宿主完成——本模块就是那个映射层。

用途：兑现 `AGENTS.md §8.2` 的「桌宠跨会话长期记忆」——把用户在酒馆里的历史对话，
经 `MemoryStore.remember_turn` 转为 HyPRA 的**情景记忆**（温层向量）与**语义事实**（冷层），
使陪伴对象「记得」认识用户之前发生过什么。

两个要点：
- **幂等**：已导入的会话 id 记在 `data/tavern_import.json`，重复导入自动跳过（`force=True` 可强制重导）；
- **不阻断**：单轮写入失败只记 warning，其余照常导入——不能因为一条坏数据放弃整批记忆。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.plugins.contracts import DataSourceMessage, DataSourceSession

logger = logging.getLogger(__name__)

#: 已导入会话的记录文件（相对 backend 运行目录；data/ 已被 .gitignore 覆盖）
DEFAULT_STATE_FILE = "data/tavern_import.json"


@dataclass
class ImportResult:
    """一次导入的统计（供 API 与 UI 展示）。"""

    sessions_total: int = 0
    sessions_imported: int = 0
    sessions_skipped: int = 0
    turns: int = 0
    facts: int = 0
    memories: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "sessions_total": self.sessions_total,
            "sessions_imported": self.sessions_imported,
            "sessions_skipped": self.sessions_skipped,
            "turns": self.turns,
            "facts": self.facts,
            "memories": self.memories,
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


class TavernMemoryImporter:
    """把酒馆会话导入为 HyPRA 的长期记忆。"""

    def __init__(self, memory_store, *, state_path: str | Path = DEFAULT_STATE_FILE) -> None:
        self.memory_store = memory_store
        self.state_path = Path(state_path)

    # ---------------- 已导入记录（幂等基础）----------------

    def _load_state(self) -> dict[str, list[str]]:
        if not self.state_path.is_file():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # 状态文件损坏：当作没导入过，不阻断
            logger.warning("酒馆导入状态读取失败（将以空记录继续）：%s", exc)
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): [str(item) for item in value]
            for key, value in data.items()
            if isinstance(value, list)
        }

    def _save_state(self, state: dict[str, list[str]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def imported_sessions(self, companion_id: str) -> list[str]:
        """某陪伴对象已导入的会话 id（UI 用于显示"已导入 N 个会话"）。"""
        return list(self._load_state().get(companion_id, []))

    def reset(self, companion_id: str = "") -> None:
        """清除导入记录（不删记忆本身）。传空串清空全部。"""
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
        force: bool = False,
        source: str = "tavern",
    ) -> ImportResult:
        """把会话导入为记忆。

        参数:
            companion_id: 记忆作用域所属的陪伴对象（`AGENTS.md §8.2` 的 `companion:{id}`）；
            force: 为 True 时忽略「已导入」记录，重新导入（用于修复或换 embedding 后重建）。
        """
        if not companion_id:
            raise ValueError("导入记忆必须指定 companion_id（记忆作用域不可为空）")

        session_list = list(sessions)
        result = ImportResult(sessions_total=len(session_list))

        state = self._load_state()
        imported = set(state.get(companion_id, []))

        for session in session_list:
            if not force and session.id in imported:
                result.sessions_skipped += 1
                continue

            turns = pair_turns(session.messages)
            if not turns:
                result.sessions_skipped += 1
                result.warnings.append(f"会话没有可用轮次（已跳过）：{session.title or session.id}")
                continue

            for turn_index, (user_text, assistant_text) in enumerate(turns):
                try:
                    stats = self.memory_store.remember_turn(
                        companion_id,
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

            result.sessions_imported += 1
            imported.add(session.id)

        state[companion_id] = sorted(imported)
        self._save_state(state)

        logger.info(
            "酒馆记忆导入完成（companion=%s）：会话 %d/%d，轮次 %d，事实 %d，情景 %d",
            companion_id,
            result.sessions_imported,
            result.sessions_total,
            result.turns,
            result.facts,
            result.memories,
        )
        return result
