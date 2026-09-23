"""纯文本与 Markdown 解析器。

两者这一步的处理完全一样（都是编码探测），但**必须分成两个实现**：
上游 chunker 按 `source_type` 决定是否把 `#` 标题当一级语义边界，
合并成一个就会丢掉 Markdown 的分块语义。
"""

from __future__ import annotations

from app.memory.knowledge.parser.base import DocumentParser, decode_text

#: 纯文本类扩展名（含日志、CSV 等实际是文本的格式）
PLAIN_SUFFIXES: frozenset[str] = frozenset({".txt", ".text", ".log", ".csv", ".tsv"})
#: Markdown 扩展名（分块时标题会作为一级语义边界）
MARKDOWN_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown", ".mdown"})


class PlainTextParser(DocumentParser):
    """纯文本：只做编码探测。"""

    name = "text"
    suffixes = PLAIN_SUFFIXES

    def extract(self, data: bytes) -> str:
        return decode_text(data)


class MarkdownParser(DocumentParser):
    """Markdown：解码同纯文本，但 `source_type` 标为 `md`。"""

    name = "md"
    suffixes = MARKDOWN_SUFFIXES

    def extract(self, data: bytes) -> str:
        return decode_text(data)
