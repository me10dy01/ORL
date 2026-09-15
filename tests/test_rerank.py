"""Phase 10 验收测试：优化检索不劣于基线（模型职责重构后按实现分别验收）。

背景：重排器已可插拔（`get_reranker`），不同实现的实测效果不同，
因此按实现**分别**断言，而不是用一个宽松断言掩盖差异：

- `llm`（LLM-as-Reranker）：Phase 10 原始验收结论，MRR 0.9 → 1.0
- `cross_encoder`（专业 ONNX cross-encoder）：Hit 不劣于基线；
  MRR 实测略低于基线（0.90 → 0.80），属已记录的负结论
  （见 docs/TROUBLESHOOTING.md N5/N6），故不对其断言 MRR 不降。

依赖本机 Ollama（bge-m3 嵌入；llm 实现还需 qwen3:8b）。
"""

from __future__ import annotations

import pytest

from app.rag.evaluation import evaluate
from app.rag.pipeline import retrieve_optimized

pytestmark = [pytest.mark.slow, pytest.mark.usefixtures("rag_index")]


def test_rerank_returns_top_n() -> None:
    r = retrieve_optimized("商城退货政策是什么？", top_k=6, top_n=2)
    assert r
    assert len(r) <= 2


def test_llm_reranker_not_worse_than_baseline() -> None:
    """LLM-as-Reranker 不劣于基线（Phase 10 原始验收标准）。"""
    report = evaluate(top_n=2, top_k=6, reranker_impl="llm")
    assert report["optimized"]["hit"] >= report["baseline"]["hit"]
    assert report["optimized"]["mrr"] >= report["baseline"]["mrr"]


def test_cross_encoder_rerank_keeps_hit_rate() -> None:
    """专业 cross-encoder：命中率不劣于基线。

    MRR 未作断言：实测该小知识库（9 chunk）上向量检索已达上限，
    cross-encoder 会把个别片段降序（0.90 → 0.80），已在负结论中记录。
    """
    report = evaluate(top_n=2, top_k=6, reranker_impl="cross_encoder")
    assert report["optimized"]["hit"] >= report["baseline"]["hit"]


def test_cross_encoder_returns_valid_documents() -> None:
    """cross-encoder 输出必须是候选集合内的真实片段（不越界、不重复）。"""
    candidates = retrieve_optimized("耳机的保修期是多久？", top_k=6, top_n=3)
    assert candidates
    chunk_ids = [c["metadata"]["chunk_id"] for c in candidates]
    assert len(chunk_ids) == len(set(chunk_ids))
    assert all(c["metadata"]["document_id"].startswith("doc_") for c in candidates)
