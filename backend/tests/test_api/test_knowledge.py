"""知识库 API 测试（TestClient，无网络）。"""

import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.main import app

client = TestClient(app)

COMPANION = "therapist"

#: 文档要足够长：短文本改几个字就会跌破 MinHash 阈值，测不出「近似重复」
DOC_A = "苏澄的咨询室在老城区一栋安静的二层小楼里，浅米色墙面，墨绿沙发。" * 15
#: 只改**第一处**（该词在文中出现 15 次，全替换会改动过多、跌出相似度阈值）
DOC_A_EDITED = DOC_A.replace("浅米色", "深灰色", 1)
#: 同一内容的两种排版：空白数量不同，但归一化后相同
DOC_A_SPACED = DOC_A.replace("。", "。 ")
DOC_A_EXTRA_SPACED = DOC_A.replace("。", "。   ")


def _upload_text(text: str, **extra):
    return client.post(
        "/knowledge/upload",
        params={"companion_id": COMPANION},
        data={"text": text, **{key: str(value).lower() for key, value in extra.items()}},
    )


def _upload_file(filename: str, content: bytes, **extra):
    return client.post(
        "/knowledge/upload",
        params={"companion_id": COMPANION},
        files={"file": (filename, content, "application/octet-stream")},
        data={key: str(value).lower() for key, value in extra.items()},
    )


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _listed() -> list[dict]:
    resp = client.get("/knowledge/list", params={"companion_id": COMPANION})
    assert resp.status_code == 200
    return resp.json()


# ---------- 上传 ----------


def test_upload_text_returns_document_info() -> None:
    resp = _upload_text(DOC_A)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["doc"]["title"] == "粘贴文本"
    assert body["doc"]["source_type"] == "text"
    assert body["doc"]["chunk_count"] >= 1
    assert body["parse_chars"] > 0
    assert body["clean_chars"] > 0
    assert body["replaced"] is False


def test_upload_file_uses_filename_as_title() -> None:
    resp = _upload_file("我的日记.txt", DOC_A.encode("utf-8"))
    assert resp.status_code == 200, resp.text

    assert resp.json()["doc"]["title"] == "我的日记"


def test_upload_markdown_marks_source_type() -> None:
    resp = _upload_file("章节.md", "# 标题\n\n正文内容。".encode("utf-8"))

    assert resp.status_code == 200
    assert resp.json()["doc"]["source_type"] == "md"


def test_custom_title_overrides_filename() -> None:
    resp = _upload_file("a.txt", b"some content", title="自定义标题")

    assert resp.json()["doc"]["title"] == "自定义标题"


# ---------- 列表与删除 ----------


def test_list_and_delete_document() -> None:
    doc_id = _upload_text(DOC_A).json()["doc"]["doc_id"]
    assert any(item["doc_id"] == doc_id for item in _listed())

    deleted = client.delete(f"/knowledge/{doc_id}", params={"companion_id": COMPANION})
    assert deleted.status_code == 200
    assert deleted.json()["removed_chunks"] >= 1

    assert all(item["doc_id"] != doc_id for item in _listed())


def test_delete_missing_document_returns_404() -> None:
    resp = client.delete("/knowledge/不存在", params={"companion_id": COMPANION})

    assert resp.status_code == 404


def test_list_is_isolated_by_companion() -> None:
    _upload_text(DOC_A)

    other = client.get("/knowledge/list", params={"companion_id": "other"}).json()
    assert other == []


# ---------- 三级去重 ----------


def test_duplicate_file_returns_409() -> None:
    content = DOC_A.encode("utf-8")
    assert _upload_file("a.txt", content).status_code == 200

    resp = _upload_file("a.txt", content)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "file"
    assert detail["similarity"] == 1.0
    assert detail["existing"]["chunk_count"] >= 1


def test_same_content_different_bytes_returns_409() -> None:
    """同内容不同排版：文件 MD5 不同，但清洗后内容 MD5 相同。

    注意 content_digest 归一的是**空白数量**（多个空格 / 换行 → 单空格），
    标点差异不算「同内容」——所以两个变体都必须是空白分隔的一致序列。
    """
    _upload_file("原始.txt", DOC_A_SPACED.encode("utf-8"))

    resp = _upload_file("副本.txt", DOC_A_EXTRA_SPACED.encode("utf-8"))

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "content"


def test_similar_document_returns_409_with_similarity() -> None:
    """改几个字的版本：两级精确哈希都不命中，靠 MinHash 抓住。"""
    _upload_text(DOC_A)

    resp = _upload_text(DOC_A_EDITED)

    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "similar"
    assert 0.85 <= detail["similarity"] <= 1.0


# ---------- force 覆盖 ----------


def test_force_replaces_instead_of_duplicating() -> None:
    """force=true 必须是**覆盖**，而不是并存两份。"""
    first = _upload_text(DOC_A).json()["doc"]

    resp = _upload_text(DOC_A_EDITED, force=True)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["replaced"] is True
    assert body["doc"]["doc_id"] == first["doc_id"]   # 复用 id，引用不失效

    listed = _listed()
    assert len(listed) == 1                          # 只剩一份


def test_force_without_duplicate_just_adds() -> None:
    """force=true 但没重复时，就是普通新增（replaced 为 false）。"""
    _upload_text(DOC_A)

    resp = _upload_text("完全不同的另一段内容。" * 30, force=True)

    assert resp.status_code == 200
    assert resp.json()["replaced"] is False
    assert len(_listed()) == 2


# ---------- 错误路径 ----------


def test_unsupported_format_returns_422() -> None:
    resp = _upload_file("图片.png", b"\x89PNG\r\n")

    assert resp.status_code == 422
    assert "不支持的文件类型" in resp.json()["detail"]


def test_scanned_pdf_returns_422_with_hint() -> None:
    """扫描件没有文字层，必须报错而不是静默入库空文档。"""
    resp = _upload_file("扫描件.pdf", _blank_pdf())

    assert resp.status_code == 422
    assert "文字层" in resp.json()["detail"]


def test_legacy_doc_returns_422_with_actionable_message() -> None:
    resp = _upload_file("旧.doc", b"\xd0\xcf\x11\xe0")

    assert resp.status_code == 422
    assert "另存为 .docx" in resp.json()["detail"]


def test_empty_input_returns_422() -> None:
    resp = client.post(
        "/knowledge/upload", params={"companion_id": COMPANION}, data={"text": "   "}
    )

    assert resp.status_code == 422


def test_missing_companion_id_returns_422() -> None:
    resp = client.post("/knowledge/upload", data={"text": "内容"})

    assert resp.status_code == 422
