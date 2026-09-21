"""知识库路由：用户上传私人语料（个人记忆层）。

    POST   /knowledge/upload        上传文档（文件或纯文本）
    GET    /knowledge/list          列出已入库文档
    DELETE /knowledge/{doc_id}      删除整篇文档（含全部分块）

处理流水线：

    上传 → ①文件 MD5 → ②解析 → ③清洗 → ④内容 MD5 → ⑤MinHash → ⑥分块 → ⑦入库

**去重命中不自动覆盖**：MinHash 是概率判重（128 个哈希的估计误差约 8.8%），
自动覆盖有误删风险；而且「这份资料你已经传过了」这个反馈本身对用户有价值。
因此返回 409 + 已有文档信息，由用户决定是否带 `force=true` 重新提交。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.api.chat import get_embedding_provider
from app.config import get_settings
from app.memory.knowledge import create_knowledge_store
from app.memory.knowledge.base import KnowledgeDoc, KnowledgeStore
from app.memory.knowledge.chunker import split_text
from app.memory.knowledge.cleaner import clean
from app.memory.knowledge.dedup import (
    MinHashSignature,
    content_digest,
    file_digest,
    find_similar,
    minhash_signature,
)
from app.memory.knowledge.parser import ParseError, parse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_store: KnowledgeStore | None = None


def get_knowledge_store() -> KnowledgeStore:
    """懒加载知识库存储。

    后端**跟随温层**配置：评审只在 .env 配一处 Qdrant，两处存储同时生效。
    embedding provider 复用 chat 模块的单例——知识库与记忆必须用同一套向量空间，
    否则两边检索结果不可比。
    """
    global _store
    if _store is None:
        settings = get_settings()
        _store = create_knowledge_store(
            settings.knowledge_backend or settings.warm_backend,
            provider=get_embedding_provider(),
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
    return _store


def set_knowledge_store(store: KnowledgeStore | None) -> None:
    """替换/重置知识库存储（测试注入用）。"""
    global _store
    _store = store


# ---------- 响应模型 ----------


class DocumentInfo(BaseModel):
    """一篇文档的元数据。"""

    doc_id: str
    title: str
    source_type: str
    chunk_count: int
    created_at: datetime
    doc_md5: str = ""
    content_md5: str = ""


class UploadResult(BaseModel):
    """上传结果（含清洗与去重过程信息）。"""

    doc: DocumentInfo
    parse_chars: int = Field(description="解析出的原始字符数")
    clean_chars: int = Field(description="清洗后的字符数")
    cleaned_ratio: float = Field(description="被清洗移除的字符占比")
    clean_hits: dict[str, int] = Field(
        default_factory=dict, description="清洗规则命中次数"
    )
    replaced: bool = Field(default=False, description="是否覆盖了已有文档（force）")


class DeleteResult(BaseModel):
    """删除结果。"""

    doc_id: str
    removed_chunks: int


def _to_info(doc: KnowledgeDoc) -> DocumentInfo:
    return DocumentInfo(
        doc_id=doc.doc_id,
        title=doc.title,
        source_type=doc.source_type,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        doc_md5=doc.doc_md5,
        content_md5=doc.content_md5,
    )


def _check_duplicate(
    store: KnowledgeStore,
    companion_id: str,
    *,
    file_md5: str,
    content_md5: str,
    signature: MinHashSignature,
    threshold: float,
) -> tuple[str, KnowledgeDoc, float] | None:
    """三级判重，返回 (级别, 已有文档, 相似度)；无重复返回 None。

    级别：file（同一文件）/ content（同内容不同格式）/ similar（近似重复）。
    """
    docs = store.list_documents(companion_id)
    if not docs:
        return None

    for doc in docs:
        if file_md5 and doc.doc_md5 == file_md5:
            return "file", doc, 1.0

    for doc in docs:
        if content_md5 and doc.content_md5 == content_md5:
            return "content", doc, 1.0

    candidates = [
        (doc.doc_id, MinHashSignature(doc.minhash)) for doc in docs if doc.minhash
    ]
    if not candidates:
        return None

    hits = find_similar(signature, candidates, threshold=threshold)
    if not hits:
        return None

    doc_id, score = hits[0]
    doc = next(item for item in docs if item.doc_id == doc_id)
    return "similar", doc, score


# ---------- 路由 ----------


@router.post("/upload", response_model=UploadResult)
async def upload_document(
    companion_id: str = Query(..., description="陪伴对象 id（记忆隔离命名空间）"),
    file: UploadFile | None = File(None),
    text: str = Form("", description="也可以直接粘贴纯文本"),
    title: str = Form("", description="自定义标题（缺省取文件名）"),
    force: bool = Form(False, description="判重命中时是否强制覆盖"),
) -> UploadResult:
    """上传一篇文档：解析 → 清洗 → 判重 → 分块 → 入库。"""
    settings = get_settings()
    if not settings.knowledge_enabled:
        raise HTTPException(status_code=503, detail="知识库功能未启用")

    # ① 取字节与文件名
    if file is not None:
        data = await file.read()
        filename = file.filename or ""
    elif text.strip():
        data = text.encode("utf-8")
        filename = "粘贴文本.txt"
    else:
        raise HTTPException(status_code=422, detail="请提供 file 或 text")

    max_bytes = int(settings.knowledge_max_file_mb * 1024 * 1024)
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件超过上限 {settings.knowledge_max_file_mb} MB",
        )

    store = get_knowledge_store()
    file_md5 = file_digest(data)

    # ② 解析（扫描版 PDF、老 .doc、编码错误都在此明确报错）
    try:
        parsed = parse(filename, data, title=title)
    except ParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # ③ 清洗去噪
    cleaned = clean(parsed.text)
    if not cleaned.text.strip():
        raise HTTPException(status_code=422, detail="清洗后没有剩余内容")

    content_md5 = content_digest(cleaned.text)
    signature = minhash_signature(cleaned.text)

    # ④ 判重：命中不自动覆盖；force=true 表示「确认覆盖」
    duplicate = _check_duplicate(
        store,
        companion_id,
        file_md5=file_md5,
        content_md5=content_md5,
        signature=signature,
        threshold=settings.knowledge_dedup_threshold,
    )
    replaced_id: str | None = None
    if duplicate is not None:
        kind, existing, score = duplicate
        if not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": kind,
                    "similarity": round(score, 4),
                    "existing": {
                        "doc_id": existing.doc_id,
                        "title": existing.title,
                        "chunk_count": existing.chunk_count,
                    },
                    "hint": "如需覆盖，请带 force=true 重新提交",
                },
            )
        # 覆盖：删掉旧文档并**复用其 id**——否则「覆盖」会变成「并存两份」
        replaced_id = existing.doc_id
        store.delete_document(companion_id, replaced_id)
        logger.info("知识库覆盖：替换已有文档 %s（判重级别 %s）", replaced_id, kind)

    # ⑤ 分块（启用语义边界时用 embedding 判断话题转折处）
    embed = (
        get_embedding_provider().embed_batch
        if settings.knowledge_semantic_chunking
        else None
    )
    chunks = split_text(
        cleaned.text,
        target=settings.knowledge_chunk_target,
        max_chars=settings.knowledge_chunk_max,
        overlap=settings.knowledge_chunk_overlap,
        embed=embed,
        semantic_threshold=settings.knowledge_semantic_threshold,
    )
    if not chunks:
        raise HTTPException(status_code=422, detail="分块后没有可入库的内容")

    # ⑥ 入库
    doc = store.add_document(
        companion_id,
        title=parsed.title,
        chunks=chunks,
        source_type=parsed.source_type,
        doc_md5=file_md5,
        content_md5=content_md5,
        minhash=signature.values,
        doc_id=replaced_id,
    )
    logger.info(
        "知识库入库：companion=%s title=%s chunks=%d（清洗移除 %.0f%%）",
        companion_id,
        doc.title,
        doc.chunk_count,
        cleaned.removed_ratio * 100,
    )

    return UploadResult(
        doc=_to_info(doc),
        parse_chars=cleaned.original_length,
        clean_chars=cleaned.cleaned_length,
        cleaned_ratio=round(cleaned.removed_ratio, 4),
        clean_hits=cleaned.hits,
        replaced=replaced_id is not None,
    )


@router.get("/list", response_model=list[DocumentInfo])
def list_documents(
    companion_id: str = Query(..., description="陪伴对象 id"),
) -> list[DocumentInfo]:
    """列出该陪伴对象已入库的全部文档。"""
    return [_to_info(doc) for doc in get_knowledge_store().list_documents(companion_id)]


@router.delete("/{doc_id}", response_model=DeleteResult)
def delete_document(
    doc_id: str,
    companion_id: str = Query(..., description="陪伴对象 id"),
) -> DeleteResult:
    """删除整篇文档及其全部分块。"""
    store = get_knowledge_store()
    if store.get_document(companion_id, doc_id) is None:
        raise HTTPException(status_code=404, detail=f"文档不存在：{doc_id}")

    removed = store.delete_document(companion_id, doc_id)
    logger.info("知识库删除：companion=%s doc=%s chunks=%d", companion_id, doc_id, removed)
    return DeleteResult(doc_id=doc_id, removed_chunks=removed)
