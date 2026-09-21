"""RAG Pipeline。

在线问答只走 retrieve_for_answer → compose_answer。
retrieve(..., rerank=True/False) 给检索评测对比基线和重排，不作为第二条在线入口。
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


def retrieve(
    query: str,
    top_k: int | None = None,
    *,
    top_n: int | None = None,
    rerank: bool = False,
    reranker_impl: str | None = None,
) -> list[dict]:
    """检索候选片段。

    rerank=False：向量 Top-K，评测基线，也是关闭重排时的召回。
    rerank=True：向量 Top-K 再重排到 Top-N。reranker_impl 只为旧评测签名保留。
    """
    k = top_k
    if k is None:
        k = settings.rerank_top_k if rerank else settings.rag_top_k
    candidates = vectorstore.retrieve(query, k)
    if not rerank:
        return candidates
    n = top_n if top_n is not None else settings.rerank_top_n
    return reranker.get_reranker(reranker_impl).rerank(query, candidates, n)


def retrieve_for_answer(query: str) -> list[dict]:
    """在线问答唯一检索入口。"""
    if settings.rag_rerank_enabled:
        return retrieve(
            query,
            top_k=settings.rerank_top_k,
            top_n=settings.rerank_top_n,
            rerank=True,
        )
    return retrieve(query, top_k=settings.rag_top_k)


def compose_answer(query: str, contexts: list[dict]) -> dict:
    """把已检索的上下文生成回答。在线 RAG 与 pipeline.answer 共用这一处。"""
    text = generation.generate_answer(query, contexts)
    return {
        "query": query,
        "answer": text,
        "sources": [c["metadata"] for c in contexts],
    }


def answer(query: str) -> dict:
    """离线/脚本入口：检索 → 生成。在线图走 RAG Agent，不从这里再写一遍生成。"""
    return compose_answer(query, retrieve_for_answer(query))
