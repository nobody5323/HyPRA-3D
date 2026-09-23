"""文档解析器工厂：按扩展名选择实现。

对应 `AGENTS.md §9.10` 的 `knowledge-parser`（builtin，可禁用/替换）。
四个实现（text / md / pdf / docx）经同一接口暴露，`parse()` 是唯一对外入口。
"""

from __future__ import annotations

from pathlib import Path

from app.memory.knowledge.parser.base import DocumentParser, ParseError, ParseResult
from app.memory.knowledge.parser.docx_parser import DocxParser
from app.memory.knowledge.parser.pdf_parser import PdfParser
from app.memory.knowledge.parser.text_parser import (
    MARKDOWN_SUFFIXES,
    PLAIN_SUFFIXES,
    MarkdownParser,
    PlainTextParser,
)

#: 解析器清单（后缀互不重叠，故顺序无关）
PARSERS: tuple[DocumentParser, ...] = (
    PlainTextParser(),
    MarkdownParser(),
    PdfParser(),
    DocxParser(),
)

#: 支持的全部扩展名（用于错误提示与 API 层校验）
SUPPORTED_SUFFIXES: frozenset[str] = frozenset(
    suffix for parser in PARSERS for suffix in parser.suffixes
)

#: 老式 `.doc`：OLE 复合二进制格式，python-docx 读不了，就地拦截
_LEGACY_DOC_SUFFIXES = frozenset({".doc"})


def select_parser(filename: str) -> DocumentParser:
    """按扩展名选解析器。

    抛出:
        ParseError: 扩展名不支持（含老式 `.doc` 与无扩展名）。
    """
    suffix = Path(filename or "").suffix.lower()

    for parser in PARSERS:
        if suffix in parser.suffixes:
            return parser

    if suffix in _LEGACY_DOC_SUFFIXES:
        # 给可执行的下一步，比抛一个用户看不懂的异常有用
        raise ParseError(
            "不支持老式 .doc 格式（二进制复合文档）："
            "请在 Word 中另存为 .docx 后重新上传"
        )
    raise ParseError(
        f"不支持的文件类型「{suffix or '无扩展名'}」：支持 .txt / .md / .pdf / .docx"
    )


def parse(filename: str, data: bytes, *, title: str = "") -> ParseResult:
    """按扩展名解析上传的文件。

    参数:
        filename: 原始文件名（用于判断格式与推断标题）；
        data: 文件字节；
        title: 显式标题（留空则取文件名主干）。

    抛出:
        ParseError: 格式不支持、可选依赖缺失、文件损坏、或无法提取到文字。
    """
    parser = select_parser(filename)

    if not parser.available():
        raise ParseError(
            f"解析 {parser.name} 需要可选依赖 {parser.dependency_hint}（当前环境未安装）："
            f"请执行 pip install {parser.dependency_hint}，或改用 .txt / .md 上传"
        )

    return parser.parse(filename, data, title=title)
