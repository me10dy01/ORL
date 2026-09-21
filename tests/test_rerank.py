"""Phase 10 验收测试：优化检索不劣于基线（模型职责重构后按实现分别验收）。

背景：项目统一使用 LLM-as-Reranker；本中文知识库的离线验收结论为
MRR 0.9 → 1.0。旧 ONNX cross-encoder 已移除，避免维护效果较差的模型路径。

依赖本机 Ollama（bge-m3 嵌入；llm 实现还需 qwen3:8b）。
"""

from __future__ import annotations

import pytest

from app.rag.evaluation import evaluate
from app.rag.pipeline import retrieve

pytestmark = [pytest.mark.slow, pytest.mark.usefixtures("rag_index")]


def test_rerank_returns_top_n() -> None:
    r = retrieve("商城退货政策是什么？", top_k=6, top_n=2, rerank=True)
    assert r
    assert len(r) <= 2


def test_llm_reranker_not_worse_than_baseline() -> None:
    """LLM-as-Reranker 不劣于基线（Phase 10 原始验收标准）。"""
    report = evaluate(top_n=2, top_k=6, reranker_impl="llm")
    assert report["optimized"]["hit"] >= report["baseline"]["hit"]
    assert report["optimized"]["mrr"] >= report["baseline"]["mrr"]


def test_llm_reranker_returns_valid_documents() -> None:
    """LLM 重排输出必须是候选集合内的真实片段（不越界、不重复）。"""
    candidates = retrieve("耳机的保修期是多久？", top_k=6, top_n=3, rerank=True)
    assert candidates
    chunk_ids = [c["metadata"]["chunk_id"] for c in candidates]
    assert len(chunk_ids) == len(set(chunk_ids))
    assert all(c["metadata"]["document_id"].startswith("doc_") for c in candidates)
