"""知识库文档加载与切分（Document → Loader → Cleaner → Chunker）。

支持多格式（md / markdown / txt / pdf / docx），见 app.rag.loaders。
metadata 遵循 AGENTS.md 第 9 条：document_id / file_name / version /
chunk_id / page / source / file_hash 均可追踪，file_hash 用于去重。
"""

from __future__ import annotations

import hashlib

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings
from app.rag import loaders
from app.seed import DOCUMENTS_DIR

settings = get_settings()

# 中文友好分隔符，避免切断句子
CHUNK_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]


def _md5(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.md5(data).hexdigest()


def load_documents() -> list[Document]:
    """加载 data/documents 下全部受支持格式，计算 file_hash 与 document_id。

    PDF 按页生成 Document（metadata.page 记录页码），其余文件单页。
    """
    docs: list[Document] = []
    for path in sorted(DOCUMENTS_DIR.iterdir()):
        if not path.is_file() or not loaders.is_supported(path):
            continue
        pages = loaders.extract_pages(path)
        full_text = "\n".join(text for _, text in pages)
        file_hash = _md5(full_text)
        document_id = f"doc_{_md5(path.name)[:8]}"
        for page, text in pages:
            if not text.strip():
                continue
            docs.append(
                Document(
                    page_content=text,
                    metadata={
                        "document_id": document_id,
                        "file_name": path.name,
                        "version": 1,
                        "source": path.name,
                        "file_hash": file_hash,
                        "page": page,
                    },
                )
            )
    return docs


def chunk_documents(docs: list[Document]) -> list[Document]:
    """把文档切分为带 chunk_id 的片段（chunk_id 在同一 document_id 内全局唯一）。"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=CHUNK_SEPARATORS,
    )
    chunks: list[Document] = []
    counters: dict[str, int] = {}
    for doc in docs:
        document_id = doc.metadata["document_id"]
        pieces = splitter.split_text(doc.page_content)
        for piece in pieces:
            index = counters.get(document_id, 0)
            counters[document_id] = index + 1
            chunks.append(
                Document(
                    page_content=piece,
                    metadata={
                        **doc.metadata,
                        "chunk_id": f"{document_id}_chunk_{index:03d}",
                        "chunk_index": index,
                    },
                )
            )
    return chunks
