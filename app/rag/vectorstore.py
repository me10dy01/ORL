"""Chroma 向量库：BGE-M3 Embedding、持久化存储、检索与去重。"""

from __future__ import annotations

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_ollama import OllamaEmbeddings

from app import profiling
from app.config import get_settings

settings = get_settings()

_store: Chroma | None = None


def get_embeddings() -> OllamaEmbeddings:
    return OllamaEmbeddings(
        model=settings.embedding_model,
        base_url=settings.ollama_base_url,
    )


def get_vectorstore() -> Chroma:
    global _store
    if _store is None:
        _store = Chroma(
            collection_name=settings.chroma_collection,
            embedding_function=get_embeddings(),
            persist_directory=settings.chroma_persist_dir,
        )
    return _store


def stale_chunk_ids(
    ids: list[str],
    metadatas: list[dict | None],
    current_hashes: dict[str, str],
) -> list[str]:
    """源文件已删除或内容已变的向量块。相同 file_name 且 file_hash 未变则保留。"""
    stale: list[str] = []
    for chunk_id, meta in zip(ids, metadatas):
        meta = meta or {}
        name = meta.get("file_name")
        if current_hashes.get(name) != meta.get("file_hash"):
            stale.append(chunk_id)
    return stale


def remove_stale_chunks(chunks: list[Document]) -> int:
    """删掉已不在当前知识库中的向量，避免旧文件名和旧正文继续被检索。"""
    store = get_vectorstore()
    current_hashes = {
        chunk.metadata["file_name"]: chunk.metadata["file_hash"] for chunk in chunks
    }
    data = store.get(include=["metadatas"])
    ids = list(data.get("ids") or [])
    metadatas = list(data.get("metadatas") or [])
    stale = stale_chunk_ids(ids, metadatas, current_hashes)
    if stale:
        store.delete(ids=stale)
    return len(stale)


def _existing_file_hashes(store: Chroma) -> set[str]:
    """返回已入库文档的 file_hash 集合，用于去重。"""
    data = store.get(include=["metadatas"])
    metadatas = data.get("metadatas") or []
    return {m.get("file_hash") for m in metadatas if m and m.get("file_hash")}


def ingest_documents(chunks: list[Document]) -> int:
    """入库：按 file_hash 跳过已存在文档，返回本次新增 chunk 数。"""
    if not chunks:
        return 0
    store = get_vectorstore()
    existing_hashes = _existing_file_hashes(store)
    new_chunks = [c for c in chunks if c.metadata["file_hash"] not in existing_hashes]
    if new_chunks:
        store.add_documents(
            new_chunks,
            ids=[c.metadata["chunk_id"] for c in new_chunks],
        )
    return len(new_chunks)


def retrieve(query: str, top_k: int | None = None) -> list[dict]:
    """Query → BGE-M3 → Chroma Top-K，返回 content + metadata（含溯源）。

    先 embed_query 再按向量检索（与 similarity_search 等价），
    以便分开统计 embedding 与向量检索的耗时。
    """
    store = get_vectorstore()
    embeddings = get_embeddings()
    with profiling.stage("embedding"):
        vector = embeddings.embed_query(query)
    with profiling.stage("retrieval"):
        docs = store.similarity_search_by_vector(
            vector, k=top_k or settings.rag_top_k
        )
    return [
        {"content": d.page_content, "metadata": d.metadata}
        for d in docs
    ]
