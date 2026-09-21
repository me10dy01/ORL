"""知识库管理用例：上传约束在这里，入库仍由 rag.management 完成。"""

from __future__ import annotations

from app.exceptions import PayloadTooLargeError, ValidationError
from app.rag import management
from app.rag.loaders import SUPPORTED_HINT, is_supported

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def list_documents() -> list[dict]:
    return management.list_documents()


def _check_upload(file_name: str, content: bytes) -> None:
    if not file_name or not is_supported(file_name):
        raise ValidationError(f"仅支持 {SUPPORTED_HINT} 格式")
    if len(content) > MAX_UPLOAD_BYTES:
        raise PayloadTooLargeError("文件大小不能超过 10MB")


def ingest_document(file_name: str, content: bytes) -> dict:
    _check_upload(file_name, content)
    return management.ingest_document(file_name, content)


def update_document(document_id: str, file_name: str, content: bytes) -> dict:
    _check_upload(file_name, content)
    return management.update_document(document_id, file_name, content)


def delete_document(document_id: str) -> dict:
    return management.delete_document(document_id)
