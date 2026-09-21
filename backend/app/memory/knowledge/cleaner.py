"""文档清洗去噪（个人记忆层）。

把不同格式（PDF / Word / Markdown / 纯文本）解析出来的原始文本归一为干净语料。

**为什么必须清洗**：格式残留会带来三重伤害——
1. 污染 **BM25 词频**：页码、页眉在所有分块里高频出现，IDF 因此失真；
2. 稀释**向量语义**：块的语义被噪音冲淡，检索精度下降；
3. 浪费 **token 预算**：注入 prompt 的每一块都有噪音占位。

**设计原则：只去噪音，不去结构。** Markdown 的 `#` 标题是分块的一级语义边界
（见 chunker.py），删掉它就等于毁掉分块质量。

管线分三层，**顺序不可颠倒**（先字符、再行、最后空白）：

    ① 字符层：零宽字符 / 控制字符 / BOM / 软连字符
    ② 行层：  HTML 实体与标签 / 页码行 / 重复出现的页眉页脚 / 英文断词连字符
    ③ 空白层：行尾空白 / 连续空格 / 多空行压缩

先做行层再做空白层是必要的：页码与页眉的判定依赖「行」的完整性，
若先把空白压扁就再也分不出行了。
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field

# ---------- ① 字符层 ----------

#: 控制字符（保留 \n \t）：\x00-\x08、\x0b、\x0c、\x0e-\x1f、\x7f
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: 零宽字符、方向控制符、BOM、软连字符（PDF 与网页复制常见的隐形垃圾）
_INVISIBLE_CHARS = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff\u00ad]")

# ---------- ② 行层 ----------

#: HTML 实体（命名实体 / 十进制 / 十六进制）
_HTML_ENTITY = re.compile(r"&(?:[a-zA-Z]{2,10}|#\d{1,6}|#x[0-9a-fA-F]{1,6});")
#: 残余 HTML 标签
_HTML_TAG = re.compile(r"<[^>]{1,120}>")
#: 英文断词连字符：`inter-\nnational` → `international`
_HYPHEN_BREAK = re.compile(r"([A-Za-z])-\n([a-z])")
#: 纯页码行：`12` / `- 12 -` / `第 12 页`
_PAGE_NUMBER = re.compile(
    r"^\s*(?:[-—–]\s*)?(?:第\s*)?\d{1,4}\s*(?:页)?(?:\s*[-—–])?\s*$"
)

# ---------- ③ 空白层 ----------

_TRAILING_SPACE = re.compile(r"[ \t\u3000]+\n")
_INLINE_SPACE = re.compile(r"[ \t\u3000]{2,}")
_MULTI_BLANK = re.compile(r"\n{3,}")

#: 重复行判定：同一行出现这么多次且足够短，才认定是页眉/页脚
_REPEAT_MIN_TIMES = 3
_REPEAT_MAX_LENGTH = 60
#: 只有总行数达到这个规模，才启用页眉页脚检测。
#: 理由：短文档里「重复的短行」更可能是**合法内容**（歌词副歌、诗歌叠句、
#: 模板段落），而页眉页脚是**多页文档**才有的现象——用行数做门槛既保留了
#: 对 PDF 噪音的检测，又不会误删这几类正当重复。
_REPEAT_MIN_LINES = 15


@dataclass
class CleanResult:
    """清洗结果（含统计，便于展示清洗效果与排查）。"""

    text: str
    original_length: int
    cleaned_length: int
    hits: dict[str, int] = field(default_factory=dict)

    @property
    def removed_ratio(self) -> float:
        """被移除字符的占比（0~1）。"""
        if self.original_length <= 0:
            return 0.0
        return max(0.0, (self.original_length - self.cleaned_length) / self.original_length)


def _is_structural(line: str) -> bool:
    """是否为应当保留的结构行（Markdown 标题 / 分隔线）。

    它们在正文里重复出现是有意义的，不能被当成页眉删掉。
    """
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    # 纯符号行（`---` / `***` / `===`）是 Markdown 分隔线
    return bool(stripped) and set(stripped) <= {"-", "*", "=", "_", " "}


def _drop_repeated_short_lines(text: str) -> tuple[str, int]:
    """删除重复出现的短行（页眉/页脚残留），返回 (文本, 删除行数)。

    两道保护，避免误删正当的重复内容：
    1. 文档行数不足 `_REPEAT_MIN_LINES` 时**整条规则不启用**（短文档不会有页眉）；
    2. 结构行（Markdown 标题 / 分隔线）永不删除。
    """
    lines = text.split("\n")
    if len(lines) < _REPEAT_MIN_LINES:
        return text, 0

    counts: Counter[str] = Counter(
        line.strip()
        for line in lines
        if line.strip() and len(line.strip()) <= _REPEAT_MAX_LENGTH
    )

    kept: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        repeated = counts.get(stripped, 0) >= _REPEAT_MIN_TIMES
        if repeated and not _is_structural(line):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def _drop_page_numbers(text: str) -> tuple[str, int]:
    """删除独立成行的页码，返回 (文本, 删除行数)。"""
    kept: list[str] = []
    removed = 0
    for line in text.split("\n"):
        if _PAGE_NUMBER.match(line):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def clean(text: str) -> CleanResult:
    """清洗一段解析后的原始文本。

    返回清洗结果与各规则的命中次数（只记录非零项）。
    """
    original = text or ""
    hits: dict[str, int] = {}

    # ---- ① 字符层 ----
    cleaned, hits["invisible_char"] = _INVISIBLE_CHARS.subn("", original)
    cleaned, hits["control_char"] = _CONTROL_CHARS.subn("", cleaned)

    # ---- ② 行层 ----
    # HTML 实体先解码再去标签：反过来会把解码产物误判成标签
    cleaned, hits["html_entity"] = _HTML_ENTITY.subn(
        lambda m: html.unescape(m.group(0)), cleaned
    )
    cleaned, hits["html_tag"] = _HTML_TAG.subn("", cleaned)
    cleaned, hits["hyphen_break"] = _HYPHEN_BREAK.subn(r"\1\2", cleaned)
    cleaned, hits["page_number"] = _drop_page_numbers(cleaned)
    cleaned, hits["repeated_line"] = _drop_repeated_short_lines(cleaned)

    # ---- ③ 空白层 ----
    cleaned, hits["trailing_space"] = _TRAILING_SPACE.subn("\n", cleaned)
    cleaned, hits["inline_space"] = _INLINE_SPACE.subn(" ", cleaned)
    cleaned, hits["multi_blank"] = _MULTI_BLANK.subn("\n\n", cleaned)
    cleaned = cleaned.strip()

    return CleanResult(
        text=cleaned,
        original_length=len(original),
        cleaned_length=len(cleaned),
        hits={name: count for name, count in hits.items() if count},
    )
