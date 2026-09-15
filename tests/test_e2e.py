"""Phase 15 最终验收：RAG / Tool / Mixed / Memory / QA / Citation / Retry / Streaming 全部通过。"""

from __future__ import annotations

import uuid

import pytest

from app.agents.main_graph import run_main_graph
from app.streaming import stream_chat

pytestmark = pytest.mark.slow


def _cid(tag: str) -> str:
    return f"e2e-{tag}-{uuid.uuid4().hex[:8]}"


def _any_keyword(text: str, keywords: list[str]) -> bool:
    """文本包含任一关键词即通过（兼容 LLM 措辞变化）。"""
    return any(kw in text for kw in keywords)


@pytest.mark.usefixtures("initialized_db", "rag_index", "fresh_cache")
def test_e2e_all_capabilities() -> None:
    # RAG + Citation + QA + Retry
    turn1 = run_main_graph("K87 键盘支持退货吗？", conversation_id=_cid("rag"))
    assert turn1["route"] == "rag"
    assert turn1["final_answer"]
    assert turn1["citations"], "RAG 回答应带溯源"
    assert all(c.get("document_id") and c.get("chunk_id") for c in turn1["citations"])
    assert turn1["qa_passed"] is True
    assert turn1["retry_count"] <= 2

    # Memory + Query Rewrite（多轮指代消解，共用会话）
    mem_cid = _cid("mem")
    run_main_graph("K87 键盘支持退货吗？", conversation_id=mem_cid)
    turn2 = run_main_graph("那运费谁承担？", conversation_id=mem_cid)
    assert _any_keyword(turn2["rewritten_query"], ["K87", "k87", "键盘"])
    assert turn2["route"] == "rag"

    # Tool
    tool = run_main_graph("查询订单 ORD20260001 的状态", conversation_id=_cid("tool"))
    assert tool["route"] == "tool"
    assert _any_keyword(tool["final_answer"], ["已发货", "发货", "出库"])

    # Mixed
    mixed = run_main_graph(
        "订单 ORD20260002 显示待发货，你们什么时候发货？", conversation_id=_cid("mixed")
    )
    assert mixed["route"] == "mixed"
    assert _any_keyword(mixed["final_answer"], ["待发货", "未发货", "尚未发货"])

    # Streaming
    events = list(stream_chat("商城退货政策是什么？", conversation_id=_cid("stream")))
    types = {e["type"] for e in events}
    assert "status" in types
    assert "done" in types
    assert any(e["type"] == "token" for e in events)
