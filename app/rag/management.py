"""知识库文档管理（Phase 13 / 扩展）：上传 / 列表 / 删除 / 修改。

供 API 层调用，不含 HTTP 细节。

知识库发生变化（新增/删除文档）时，会清空回答缓存，避免返回过期答案。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app import cache
from app.exceptions import NotFoundError, ValidationError
from app.rag import documents, loaders, pipeline, vectorstore
from app.seed import DOCUMENTS_DIR


def _safe_target(file_name: str) -> Path:
    """把上传文件名约束到 data/documents 目录内（防路径穿越）。"""
    safe_name = Path(file_name).name
    if not safe_name or safe_name in {".", ".."}:
        raise ValidationError(f"非法文件名: {file_name}")
    base = DOCUMENTS_DIR.resolve()
    target = (base / safe_name).resolve()
    if target.parent != base:
        raise ValidationError(f"非法文件路径: {file_name}")
    return target


def save_document(file_name: str, content: bytes) -> Path:
    """把上传内容保存到 data/documents。"""
    if not content:
        raise ValidationError("文件内容不能为空")
    path = _safe_target(file_name)
    path.write_bytes(content)
    return path


def ingest_document(file_name: str, content: bytes) -> dict:
    """保存并增量入库（按 file_hash 去重）；有新内容时让回答缓存失效。"""
    path = save_document(file_name, content)
    result = pipeline.build_index()
    result["file_name"] = path.name
    if result.get("ingested_chunks") or result.get("removed_chunks"):
        cache.clear()
    return result


def list_documents() -> list[dict]:
    """按 document_id 聚合列出已入库文档。"""
    store = vectorstore.get_vectorstore()
    data = store.get(include=["metadatas"])
    metadatas = data.get("metadatas") or []

    agg: dict[str, dict] = {}
    for meta in metadatas:
        if not meta:
            continue
        doc_id = meta.get("document_id")
        if doc_id not in agg:
            agg[doc_id] = {
                "document_id": doc_id,
                "file_name": meta.get("file_name"),
                "file_hash": meta.get("file_hash"),
                "chunk_count": 0,
            }
        agg[doc_id]["chunk_count"] += 1
    return list(agg.values())


def _find_document(document_id: str) -> dict:
    """按 document_id 查找已入库文档，不存在则抛 NotFoundError。"""
    for doc in list_documents():
        if doc["document_id"] == document_id:
            return doc
    raise NotFoundError(f"文档不存在: {document_id}")


def _delete_vectors(document_id: str) -> int:
    """按 document_id 删除其全部向量，返回删除数量（不动源文件）。"""
    store = vectorstore.get_vectorstore()
    data = store.get(where={"document_id": document_id})
    ids = data.get("ids", [])
    if ids:
        store.delete(ids=ids)
        cache.clear()
    return len(ids)


def _remove_source_file(file_name: str | None) -> bool:
    """删除源文件；仅当已入库文档中不再有同名文档时才删，避免误删新版本。"""
    if not file_name:
        return False
    if any(d["file_name"] == file_name for d in list_documents()):
        return False
    safe_name = Path(file_name).name
    if not safe_name or safe_name in {".", ".."}:
        return False
    path = DOCUMENTS_DIR / safe_name
    path = path.resolve()
    if path.parent != DOCUMENTS_DIR.resolve():
        return False
    if path.is_file():
        path.unlink()
        return True
    return False


def delete_document(document_id: str) -> dict:
    """按 document_id 删除文档：移除全部向量，并在无同名文档时清理源文件。"""
    doc = _find_document(document_id)
    deleted = _delete_vectors(document_id)
    file_removed = _remove_source_file(doc.get("file_name"))
    return {
        "document_id": document_id,
        "file_name": doc.get("file_name"),
        "deleted_chunks": deleted,
        "file_removed": file_removed,
    }


def _hash_upload(file_name: str, content: bytes) -> str:
    """把上传内容落临时文件后提取文本，计算与入库一致的 file_hash。"""
    suffix = Path(file_name).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        pages = loaders.extract_pages(tmp_path)
        full_text = "\n".join(text for _, text in pages)
        return documents._md5(full_text)
    finally:
        tmp_path.unlink(missing_ok=True)


def update_document(document_id: str, file_name: str, content: bytes) -> dict:
    """替换文档内容（修改按钮）。

    - 同名替换：document_id 由文件名派生、保持不变；先清旧版本向量再入库。
    - 换名替换：入库新文档后删除旧文档（向量 + 源文件）。
    - 内容未变化（file_hash 相同）：幂等返回 changed=False，不做任何写操作。
    """
    if not loaders.is_supported(file_name):
        raise ValidationError(f"仅支持 {loaders.SUPPORTED_HINT} 格式")
    old = _find_document(document_id)

    new_hash = _hash_upload(file_name, content)
    if new_hash == old.get("file_hash"):
        return {
            "changed": False,
            "old_document_id": document_id,
            "new_document_id": document_id,
            "file_name": old.get("file_name"),
            "deleted_chunks": 0,
            "ingested_chunks": 0,
        }

    same_name = file_name == old.get("file_name")
    if same_name:
        # 同名：document_id 不变。先清旧版本（防止新旧 chunk 混存），再入库新内容。
        deleted = _delete_vectors(document_id)
        result = ingest_document(file_name, content)
        return {
            "changed": True,
            "old_document_id": document_id,
            "new_document_id": document_id,
            "file_name": file_name,
            "deleted_chunks": deleted,
            "ingested_chunks": result.get("ingested_chunks", 0),
            "documents": result.get("documents", 0),
            "chunks": result.get("chunks", 0),
        }

    # 换名：先入库新文档，成功后再删旧文档（入库失败时旧文档保持完好）。
    result = ingest_document(file_name, content)
    deleted = _delete_vectors(document_id)
    _remove_source_file(old.get("file_name"))
    return {
        "changed": True,
        "old_document_id": document_id,
        "new_document_id": f"doc_{documents._md5(file_name)[:8]}",
        "file_name": file_name,
        "deleted_chunks": deleted,
        "ingested_chunks": result.get("ingested_chunks", 0),
        "documents": result.get("documents", 0),
        "chunks": result.get("chunks", 0),
    }
