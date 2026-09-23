"""PDF 解析器（可选依赖 `pypdf`，**惰性导入**）。

惰性导入是这次拆分顺带修掉的缺陷：原实现在模块顶层 `from pypdf import PdfReader`，
少装一个可选包会让整个 `parser` 模块 import 失败——连带 .txt / .md 上传也起不来。
现在缺依赖只影响 PDF 这一个通路。
"""

from __future__ import annotations

import io

from app.memory.knowledge.parser.base import DocumentParser, ParseError


class PdfParser(DocumentParser):
    """提取 PDF 的文字层。"""

    name = "pdf"
    suffixes = frozenset({".pdf"})
    dependency_hint = "pypdf"

    def available(self) -> bool:
        try:
            import pypdf  # noqa: F401, PLC0415 - 可选依赖探测
        except ImportError:
            return False
        return True

    def extract(self, data: bytes) -> str:
        from pypdf import PdfReader  # noqa: PLC0415 - 可选依赖

        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:  # noqa: BLE001 - 第三方库异常类型不稳定
            raise ParseError(f"PDF 解析失败：{exc}") from exc

        text = "\n".join(pages)
        if not text.strip():
            # 最常见的一类失败：用户以为传上去了，实际上库里只有空白
            raise ParseError(
                "该 PDF 没有可提取的文字层（可能是扫描件或图片型 PDF）。"
                "请先用 OCR 工具转成文本，或直接上传 .txt / .md"
            )
        return text
