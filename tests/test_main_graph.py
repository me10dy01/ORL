"""Phase 07/08/09 验收：路由三类任务 + 子图嵌入 + 多轮 Query Rewrite。"""

from __future__ import annotations

import uuid

import pytest

from app.agents.main_graph import build_main_graph, run_main_graph

pytestmark = [
    pytest.mark.slow,
    pytest.mark.usefixtures("initialized_db", "rag_index", "fresh_cache"),
]


def _cid() -> str:
    return f"t-{uuid.uuid4().hex[:8]}"


def _any_keyword(text: str, keywords: list[str]) -> bool:
    """文本包含任一关键词即通过（兼容 LLM 措辞变化）。"""
    return any(kw in text for kw in keywords)


def test_routes_rag_question() -> None:
    r = run_main_graph("商城退货政策是什么？", conversation_id=_cid())
    assert r["route"] == "rag"
    assert _any_keyword(r["final_answer"], ["退货", "无理由", "七天", "签收"])
    assert r["citations"]


def test_routes_tool_question() -> None:
    r = run_main_graph("查询订单 ORD20260001 的状态", conversation_id=_cid())
    assert r["route"] == "tool"
    assert _any_keyword(r["final_answer"], ["已发货", "发货", "出库"])


def test_routes_mixed_question() -> None:
    r = run_main_graph(
        "订单 ORD20260002 显示待发货，你们什么时候发货？", conversation_id=_cid()
    )
    assert r["route"] == "mixed"
    agents = {t["agent"] for t in r["tasks"]}
    assert agents == {"rag", "tool"}
    assert _any_keyword(r["final_answer"], ["待发货", "未发货", "尚未发货"])
    assert _any_keyword(r["final_answer"], ["小时", "h", "24"])
    assert r["citations"]


def test_main_graph_embeds_subgraphs() -> None:
    """Phase 08 验收：主图以子图节点嵌入 RAG / Tool，不包含内部实现。"""
    graph = build_main_graph()
    nodes = graph.get_graph().nodes
    assert "rag" in nodes
    assert "tool" in nodes
    assert "supervisor" in nodes
    assert "synthesis" in nodes


def test_multi_turn_rewrites_reference() -> None:
    """Phase 09 验收：多轮指代能被正确改写。"""
    cid = _cid()
    run_main_graph("K87 键盘支持退货吗？", conversation_id=cid)
    r2 = run_main_graph("那运费谁承担？", conversation_id=cid)
    assert r2["route"] == "rag"
    assert _any_keyword(r2["final_answer"], ["运费", "快递费", "邮费", "物流"])
    assert _any_keyword(r2["rewritten_query"], ["K87", "k87", "键盘"])


def test_qa_and_retry_limited() -> None:
    """Phase 11 验收：回答经过 QA，重试次数不超过 MAX_RETRY。"""
    r = run_main_graph("商城退货政策是什么？", conversation_id=_cid())
    assert "qa_passed" in r
    assert r["retry_count"] <= 2
