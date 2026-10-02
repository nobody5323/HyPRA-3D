"""每轮「注入了什么」的落盘留痕（排查「记忆串」时的唯一实证手段）。

为什么需要它：用户问「它怎么知道我聊过这个？」时，事后只有两条路——复现，
或者翻出**那一轮实际注入的内容**。而 system_prompt 只随响应回给前端、不落盘，
后端一重启就无从考证，于是这类问题只能靠猜。

所以这里把每轮的召回结果（命中条目 id / 情景记忆行 / 事实行 / 个人记忆行）
与 system_prompt 的指纹写成 JSONL（一行一轮）：

- 排查时直接 `grep` 角色 id 或关键词，就能看到那一轮到底注入了什么；
- 不写 whole prompt（体积大且可能含用户隐私原文），只写召回行 + 长度 + sha1，
  需要对比两轮是否同一份提示词时用指纹即可。

写入是**尽力而为**：出错只记 warning，绝不影响对话本身。文件超过
`_MAX_BYTES` 时轮转一次（只保留上一份），避免无限增长。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: 单个留痕文件的大小上限（超过就轮转，只留上一份）
_MAX_BYTES = 5 * 1024 * 1024


def record_turn(
    trace_path: str | Path,
    *,
    session_id: str,
    companion_id: str,
    user_input: str,
    mode: str = "",
    worldbook_ids: list[str],
    warm_lines: list[str],
    fact_lines: list[str],
    knowledge_lines: list[str],
    system_prompt: str,
) -> None:
    """追加一轮注入留痕（JSONL）。任何写入异常都不外抛。"""
    try:
        path = Path(trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > _MAX_BYTES:
            # 只留上一份：留痕是排查用，不需要完整历史
            path.replace(path.with_name(path.name + ".1"))

        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session_id": session_id,
            "companion_id": companion_id,
            # 交互模式：查「为什么这轮没有/有酒馆世界书」时必须先看它
            # （酒馆来源知识只在 tavern 模式召回，见 app/memory/knowledge/scopes）
            "mode": mode,
            "user_input": user_input[:500],
            "worldbook_ids": worldbook_ids,
            "warm_lines": warm_lines,
            "fact_lines": fact_lines,
            "knowledge_lines": knowledge_lines,
            "system_prompt_chars": len(system_prompt),
            "system_prompt_sha1": hashlib.sha1(system_prompt.encode("utf-8")).hexdigest(),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover - 磁盘满 / 权限问题
        logger.warning("记忆留痕写入失败：%s", exc)
