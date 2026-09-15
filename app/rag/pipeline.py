"""RAG Pipeline。

Baseline（Phase 04）：Document → Chunk → BGE-M3 → Chroma → Retrieval → Qwen3
Optimized（Phase 10）：Vector Top-K → Reranker → Top-N → Qwen3
"""

from __future__ import annotations

from app.config import get_settings
from app.rag import documents, generation, reranker, vectorstore

settings = get_settings()


def build_index() -> dict:
    """构建知识库索引，返回统计信息。"""
    docs = documents.load_documents()
    chunks = documents.chunk_documents(docs)
    ingested = vectorstore.ingest_documents(chunks)
    return {
        "documents": len(docs),
        "chunks": len(chunks),
        "ingested_chunks": ingested,
    }


def retrieve(query: str, top_k: int | None = None) -> list[dict]:
    """Baseline 检索：向量 Top-K。"""
    return vectorstore.retrieve(query, top_k)


def retrieve_baseline(query: str, top_n: int | None = None) -> list[dict]:
    """Baseline 检索：直接向量 Top-N。"""
    return vectorstore.retrieve(query, top_n or settings.rag_top_k)


def retrieve_optimized(
    query: str,
    top_k: int | None = None,
    top_n: int | None = None,
    reranker_impl: str | None = None,
) -> list[dict]:
    """Optimized 检索：向量 Top-K → Reranker → Top-N。

    reranker_impl 为空时使用配置（`RERANKER_IMPL`）；显式传入可用于
    对比不同重排实现（如 "llm" 与 "cross_encoder"）。
    """
    k = top_k or settings.rerank_top_k
    n = top_n or settings.rerank_top_n
    candidates = vectorstore.retrieve(query, k)
    return reranker.get_reranker(reranker_impl).rerank(query, candidates, n)


def answer(query: str, top_k: int | None = None) -> dict:
    """端到端（baseline）：检索 → 生成 → 回答 + 溯源。"""
    contexts = retrieve(query, top_k)
    text = generation.generate_answer(query, contexts)
    return {
        "query": query,
        "answer": text,
        "sources": [c["metadata"] for c in contexts],
    }
