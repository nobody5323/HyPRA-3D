"""ST 宏解析（契约文档 §11）。

与项目已有的状态变量宏（`state_vars/resolver.py` 的 `{{current_mood}}` 等）**分开实现**：

- 名字空间与语义不同：ST 用 `{{char}}` 而非 `{{char_name}}`，且有函数式 `{{getvar::}}`；
- 独立模块可保证**内置分层路径零回归**——只有 ST 组装路径（renderer）调用本模块。

支持清单：

| 类别 | 宏 |
|---|---|
| 变量替换 | `{{char}}` `{{user}}` `{{description}}` `{{personality}}` `{{scenario}}` `{{persona}}` |
| 时间 | `{{time}}` `{{date}}` |
| 文本处理 | `{{//注释}}`（输出空） `{{trim}}`（连同所在行的换行一起删除） |
| 会话变量 | `{{getvar::x}}` `{{setvar::x::v}}` `{{addvar::x::v}}` `{{incvar::x}}` `{{decvar::x}}` |
| 随机 | `{{random::a,b}}`（无参数 = 0~1 浮点） `{{pick::a,b}}`（同一 seed 下稳定） `{{roll::1d20}}` |

未知宏**原样保留**并计入 `unresolved`（不静默吞掉）——正文里的普通 `{{ }}` 文本
（例如模型自己的模板示例）不会被误删。
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from datetime import datetime

__all__ = ["MacroContext", "render_macros"]

# 宏体不含花括号，避免正文里相隔很远的 {{ 与 }} 被误配成一个大宏
_MACRO_PATTERN = re.compile(r"\{\{([^{}]*?)\}\}")

# 独占一行的 {{trim}}：连同该行的空白与换行一并删除
_TRIM_LINE_PATTERN = re.compile(r"[ \t]*\{\{\s*trim\s*\}\}[ \t]*\r?\n?")

# {{roll::2d6+3}} / {{roll::1d20}} / {{roll::d20}}
_ROLL_PATTERN = re.compile(r"^\s*(?:(\d+)\s*)?d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)


@dataclass
class MacroContext:
    """宏解析所需的运行时值。

    `variables` 是**会话级**可变字典：`{{setvar::}}` / `{{incvar::}}` 会就地修改它，
    因此调用方（P4 的 graph 节点）应按会话持有同一份字典，实现变量隔离。
    """

    char_name: str = ""
    user_name: str = ""
    description: str = ""
    personality: str = ""
    scenario: str = ""
    persona: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    now: datetime | None = None
    # {{pick::}} 的稳定性种子（同一 seed 下同一条宏多次渲染结果一致）
    pick_seed: str = ""
    # 注入随机源（测试可传固定种子；缺省用模块级 random）
    rng: random.Random | None = None


def render_macros(text: str, context: MacroContext | None = None) -> tuple[str, list[str]]:
    """解析文本中的 ST 宏，返回 (结果文本, 未识别宏列表)。

    未识别宏**保留原文**（宁可让用户看见 `{{unknown}}`，也不要悄悄删掉内容）。
    """
    if not text:
        return text, []

    ctx = context or MacroContext()
    unresolved: list[str] = []

    cleaned = _TRIM_LINE_PATTERN.sub("", text)

    def _replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        body = match.group(1).strip()
        if not body:
            return raw
        if body.startswith("//"):
            return ""   # {{//注释}}
        name, separator, args_text = body.partition("::")
        return _dispatch(name.strip().lower(), args_text if separator else "", raw, ctx, unresolved)

    resolved = _MACRO_PATTERN.sub(_replace, cleaned)
    return resolved, unresolved


# --------------------------------------------------------------------------
# 分派
# --------------------------------------------------------------------------


def _dispatch(
    name: str,
    args_text: str,
    raw: str,
    ctx: MacroContext,
    unresolved: list[str],
) -> str:
    """按宏名分派；不认识的宏返回原文并记录。"""
    # ---- 变量替换类 ----
    if name == "char":
        return ctx.char_name
    if name == "user":
        return ctx.user_name
    if name == "description":
        return ctx.description
    if name == "personality":
        return ctx.personality
    if name == "scenario":
        return ctx.scenario
    if name == "persona":
        return ctx.persona

    # ---- 时间类 ----
    if name == "time":
        return _now(ctx).strftime("%H:%M")
    if name == "date":
        return _now(ctx).strftime("%Y-%m-%d")

    # ---- 会话变量类 ----
    args = args_text.split("::") if args_text else []
    if name == "getvar":
        return ctx.variables.get(args[0].strip(), "") if args else ""
    if name == "setvar":
        if len(args) >= 2:
            ctx.variables[args[0].strip()] = args[1]
        return ""
    if name == "addvar":
        if len(args) >= 2:
            key = args[0].strip()
            ctx.variables[key] = ctx.variables.get(key, "") + args[1]
        return ""
    if name in ("incvar", "decvar"):
        if args:
            key = args[0].strip()
            try:
                current = int(ctx.variables.get(key, "0") or "0")
            except ValueError:
                current = 0
            current += 1 if name == "incvar" else -1
            ctx.variables[key] = str(current)
            return str(current)
        return ""

    # ---- 随机类 ----
    if name == "random":
        return _random_pick(args_text, ctx, stable=False)
    if name == "pick":
        return _random_pick(args_text, ctx, stable=True)
    if name == "roll":
        return _roll(args_text or "1d20", ctx)

    unresolved.append(raw)
    return raw


def _now(ctx: MacroContext) -> datetime:
    return ctx.now or datetime.now()


def _random_pick(args_text: str, ctx: MacroContext, *, stable: bool) -> str:
    """`{{random::a,b}}` / `{{pick::a,b}}`。

    `pick` 用种子做稳定哈希——同一条消息里多次渲染结果一致（ST 的 pick 语义）；
    `random` 每次重新随机。无参数时返回 0~1 的浮点数（对齐 ST）。
    """
    if not args_text.strip():
        return str(round(_rng(ctx).random(), 4))

    options = [option.strip() for option in args_text.split(",")]
    if len(options) == 1:
        return options[0]

    if stable:
        digest = hashlib.md5(f"{ctx.pick_seed}|{args_text}".encode("utf-8")).hexdigest()
        index = int(digest[:8], 16) % len(options)
    else:
        index = _rng(ctx).randrange(len(options))
    return options[index]


def _roll(expression: str, ctx: MacroContext) -> str:
    """`{{roll::2d6+3}}` 掷骰（不匹配表达式时原样返回 `{{roll::…}}` 之外的内容）。"""
    match = _ROLL_PATTERN.match(expression)
    if match is None:
        return expression.strip()

    count = int(match.group(1)) if match.group(1) else 1
    sides = int(match.group(2))
    if count <= 0 or sides <= 0:
        return "0"
    modifier = int(match.group(3).replace(" ", "")) if match.group(3) else 0

    total = sum(_rng(ctx).randint(1, sides) for _ in range(count)) + modifier
    return str(total)


def _rng(ctx: MacroContext) -> random.Random:
    return ctx.rng or random
