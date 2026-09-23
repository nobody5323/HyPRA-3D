"""文档解析：把上传的文件转成纯文本（随后交给 cleaner 清洗）。

拆分自原 `parser.py`（`AGENTS.md §9.11` P2）：txt / md / pdf / docx 各一个实现，
由 `factory.select_parser()` 按扩展名选择。**对外接口保持不变**——

    from app.memory.knowledge.parser import ParseError, parse

两个明确的能力边界（遇到时**报错**，而不是静默入库空文档）：

1. **扫描版 PDF**（图片型）没有文字层，`extract_text()` 返回空。
   这是最常见的一类失败：用户以为传上去了，实际上库里只有空白。
2. **老式 `.doc`** 是 OLE 复合二进制格式，`python-docx` 读不了。

本模块是兼容层：既有调用方（`api/knowledge.py`）与测试都按旧路径 import，
重导出让这次拆分对它们完全透明。
"""

from app.memory.knowledge.parser.base import (
    DocumentParser,
    ParseError,
    ParseResult,
    decode_text,
    title_from,
)
from app.memory.knowledge.parser.docx_parser import DocxParser
from app.memory.knowledge.parser.factory import (
    PARSERS,
    SUPPORTED_SUFFIXES,
    parse,
    select_parser,
)
from app.memory.knowledge.parser.pdf_parser import PdfParser
from app.memory.knowledge.parser.text_parser import (
    MARKDOWN_SUFFIXES,
    PLAIN_SUFFIXES,
    MarkdownParser,
    PlainTextParser,
)

__all__ = [
    "MARKDOWN_SUFFIXES",
    "PARSERS",
    "PLAIN_SUFFIXES",
    "SUPPORTED_SUFFIXES",
    "DocxParser",
    "DocumentParser",
    "MarkdownParser",
    "ParseError",
    "ParseResult",
    "PdfParser",
    "PlainTextParser",
    "decode_text",
    "parse",
    "select_parser",
    "title_from",
]
