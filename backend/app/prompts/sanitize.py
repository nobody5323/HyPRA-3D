"""模型输出清理（兜底）。

问题背景（实测）：
1. 模型（尤其推理模型）会把 markdown 格式与 emoji 写进回复；
2. 更严重的是**自我纠正泄漏**——例如「🌱（注：虽然不能加 emoji 但是语气可以很软）
   → actually no emoji allowed per instructions! Corrected below.」，
   即模型在正文里写下它对规则的检查与纠正过程；
3. 还有一种**形态跑偏**：模型把一对一对话写成小说段落，回复里带上旁白、动作与
   心理描写（`*她轻轻笑了笑*`、`（他把杯子推过来）`、`心里想着……`）。

提示词已用**正向指令**要求纯对话形态（第一道防线，见 `prompt_manager.DIALOGUE_ONLY_RULE`），
本模块做最后兜底：剥离格式标记、emoji、元评论行与**明显被标点包裹的旁白 / 动作**——
只保留对用户说的话。

保守策略：只删「被明确标记出来的东西」，不改写任何正常语句。因此旁白清理只认
**有包裹符号的**写法（星号、括号），不做语义判断——模型不用标记直接写散文式旁白时
本层无能为力，那属于提示词层的职责。
"""

import re

# 元评论整行（模型对规则/指令的自述与自我纠正）——整行删除
_META_LINE = re.compile(
    r"^.*(?:"
    r"emoji|Emoji|EMOJI|instructions|Instruction|corrected|Corrected|"
    r"按指令|按要求|已按要求|已修正|虽然不能|不能加|规则检查|自我纠正"
    r").*$",
    re.MULTILINE,
)
# 行首列表符号：- / * / + / • / 1. / 1、 / 1)
_LIST_MARK = re.compile(r"^[ \t]*(?:[-*+•]|\d+[.、)])\s+", re.MULTILINE)
# 标题符号
_HEADING = re.compile(r"^[ \t]*#{1,6}\s+", re.MULTILINE)
# 分隔线
_DIVIDER = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.MULTILINE)
# 行内代码
_INLINE_CODE = re.compile(r"`([^`]+)`")
# emoji / 符号表情（常见 Unicode 区间）
_EMOJI = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff\u2b00-\u2bff\ufe0f]"
)

# ---- 旁白 / 动作（形态兜底）----
# 星号包裹的动作：`*她笑了笑*` / `＊叹气＊`（半角与全角星号都算）。
# 注意它在**强调标记剥离之后**才执行：`**很重要**` 先被上面处理成纯文本，
# 不会误伤成动作（单个 `*` 在正常口语里几乎不出现，误删风险可接受）。
_ACTION_MARK = re.compile(r"[*＊][^*＊\n]{1,200}[*＊]")
# 整行括号旁白：`（她把杯子推过来）` 独占一行时整行删掉
_PAREN_LINE = re.compile(r"^[ \t]*[（(][^\n]*[）)][ \t]*$", re.MULTILINE)
# 行内短括号插注：`（笑）` / `(叹气)`
_INLINE_PAREN = re.compile(r"[（(]([^（()）\n]{1,24})[）)]")


def _tidy(text: str) -> str:
    """收尾清理：行尾空白、多余空行、首尾空白。"""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _drop_inline_narration(match: re.Match) -> str:
    """行内短括号插注：不含数字 / 拉丁字母的才当旁白删。

    `（笑）`、`（轻轻叹了口气）` 删；`（4-7-8 呼吸法）`、`（PM2.5）` 留。
    这是个**保守的启发式**：正常对话里极少用括号做补充说明，而括号裹动作是
    角色扮演最常见的写法，所以宁可漏一点、也不要误删用户真正需要的信息。
    """
    inner = match.group(1)
    return "" if not re.search(r"[0-9A-Za-z]", inner) else match.group(0)


def _strip_narration(text: str) -> str:
    """剥离被标点包裹的旁白 / 动作（星号动作 → 整行括号 → 行内短括号）。"""
    text = _ACTION_MARK.sub("", text)
    text = _PAREN_LINE.sub("", text)
    return _INLINE_PAREN.sub(_drop_inline_narration, text)


def sanitize_reply(text: str) -> str:
    """清理 assistant 回复：剥离格式标记、emoji、元评论行与旁白 / 动作。

    保守策略：只做「去标记」「删元评论行」与「删被包裹的旁白」，不改写任何正常语句。
    """
    if not text:
        return text

    cleaned = _META_LINE.sub("", text)          # 先删元评论（避免干扰后续处理）
    cleaned = _DIVIDER.sub("", cleaned)
    cleaned = _HEADING.sub("", cleaned)
    cleaned = _LIST_MARK.sub("", cleaned)
    cleaned = _INLINE_CODE.sub(r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "")   # 强调标记（先于星号动作）
    cleaned = _EMOJI.sub("", cleaned)
    cleaned = _tidy(cleaned)

    dialogue = _strip_narration(cleaned)
    # 模型整条回复都是旁白时（`*她笑了笑*`），清理后会得到空串。这里**回退到原文**：
    # 空回复在链路上会被当成「模型没反应」，触发额度重试甚至给用户一片空白——
    # 那比漏掉一句旁白严重得多。宁可形态不干净，也不能让这一轮没有回应。
    return _tidy(dialogue) if dialogue.strip() else cleaned


def has_meta_comment(text: str) -> bool:
    """文本是否含元评论（供测试与调试）。"""
    return bool(_META_LINE.search(text or ""))
