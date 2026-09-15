"""RAG 模块（Phase 04 Baseline）。

对外提供：
    build_index()  —— Document → Chunk → BGE-M3 → Chroma
    retrieve()     —— Query → BGE-M3 → Chroma Top-K
    answer()       —— Retrieval → Qwen3 → 回答 + 溯源
"""

from app.rag.pipeline import answer, build_index, retrieve

__all__ = ["build_index", "retrieve", "answer"]
