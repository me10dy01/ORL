"""Phase 05 验收测试：RAG Agent 能独立运行，输出 answer + sources。"""

from __future__ import annotations

import pytest

from app.agents.rag_agent import RAGState, build_rag_agent, run_rag_agent

pytestmark = pytest.mark.slow


@pytest.mark.usefixtures("rag_index")
def test_rag_agent_runs_independently() -> None:
    result = run_rag_agent("K87 键盘支持退货吗？")
    assert isinstance(result["answer"], str) and result["answer"]
    assert result["sources"]
    for s in result["sources"]:
        assert s["document_id"].startswith("doc_")
        assert s["chunk_id"]
        assert s["file_name"]


@pytest.mark.usefixtures("rag_index")
def test_rag_agent_no_answer_when_no_context() -> None:
    """检索不到依据时明确说明，不编造。"""
    result = run_rag_agent("商城是否支持七天无理由退款的量子计算机？")
    assert result["answer"]


@pytest.mark.usefixtures("rag_index")
def test_build_rag_agent_has_two_nodes() -> None:
    graph = build_rag_agent()
    nodes = graph.get_graph().nodes
    assert "retrieve" in nodes
    assert "generate" in nodes
