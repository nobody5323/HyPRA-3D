"""文档解析器接口与共用工具。

拆分自原 `parser.py`（`AGENTS.md §9.11` P2）：**一种格式一个实现**，
由 `factory.select_parser()` 按扩展名选择。

职责边界（不变）：解析结果**不做任何清洗**——parser 负责「取出文字」，
cleaner 负责「去噪音」，两者的测试各自独立。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

#: 文本解码的候选编码（按顺序尝试；utf-8-sig 同时处理 BOM）
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


class ParseError(ValueError):
    """解析失败（格式不支持 / 依赖缺失 / 文件损坏 / 无可提取文字）。"""


@dataclass
class ParseResult:
    """解析结果。"""

    text: str
    #: 来源格式：text | md | pdf | docx
    source_type: str
    #: 文档标题（默认取文件名主干，可被调用方覆盖）
    title: str


def title_from(filename: str) -> str:
    """从文件名推断标题（去扩展名）。"""
    return Path(filename or "").stem.strip() or "未命名文档"


def decode_text(data: bytes) -> str:
    """按候选编码解码文本（中文常见 GB18030）。"""
    for encoding in TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ParseError("无法识别文本编码（支持 UTF-8 与 GB18030）")


class DocumentParser(ABC):
    """文档解析器接口（一种格式一个实现）。"""

    #: 实现名，同时用作 `ParseResult.source_type`（chunker 依赖它判断分块策略）
    name: str = ""

    #: 本实现负责的扩展名（小写，含点）
    suffixes: frozenset[str] = frozenset()

    #: 可选依赖名（缺依赖时用于给出可执行的安装提示）；纯标准库实现留空
    dependency_hint: str = ""

    def available(self) -> bool:
        """依赖是否就绪。

        缺可选依赖时返回 False（而不是让 import 崩掉）——这样 pdf 装不上
        也不会连累 txt / md 的上传通路。
        """
        return True

    @abstractmethod
    def extract(self, data: bytes) -> str:
        """从字节中取出纯文本（不清洗）。"""

    def parse(self, filename: str, data: bytes, *, title: str = "") -> ParseResult:
        """模板方法：空文件校验 → 提取 → 空文本校验 → 组装结果。

        共用的校验放基类、实现只管 `extract()`——否则每个格式都要重复一遍，
        而且很容易漏掉其中一道。
        """
        if not data:
            raise ParseError("文件内容为空")

        text = self.extract(data)
        if not text.strip():
            raise ParseError("文件没有可提取的文字内容")

        return ParseResult(
            text=text,
            source_type=self.name,
            title=title.strip() or title_from(filename),
        )
