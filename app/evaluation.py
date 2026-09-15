"""系统级评估（Phase 15 / 模型职责重构）。

对 data/eval/test_questions.json 运行完整 Workflow，计算指标：
    Routing Accuracy / Tool Calling Accuracy / Retrieval Hit Rate /
    Citation Accuracy / Answer Accuracy / QA Pass Rate / Retry Rate / Latency
并聚合各阶段耗时（avg / P95），用于模型职责重构前后的性能对比。
"""

from __future__ import annotations

import logging
import statistics
import time

from app import cache, memory
from app.agents.main_graph import run_main_graph
from app.seed import TestQuestion, load_test_questions

logger = logging.getLogger(__name__)


def _keyword_hit(answer: str, keywords: list[str]) -> bool:
    """回答命中任一关键词即视为正确；无关键词时只要非空即可。"""
    if not answer:
        return False
    if not keywords:
        return True
    return any(k in answer for k in keywords)


def _p95(values: list[float]) -> float:
    """P95 延迟（样本少时取最大值，避免插值失真）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def evaluate_question(
    question: TestQuestion, conversation_id: str, align: bool = False
) -> dict:
    """运行单个问题并返回逐项结果。多轮问题先写入历史。"""
    for prior in question.history:
        memory.add_message(conversation_id, "user", prior)

    start = time.perf_counter()
    result = run_main_graph(
        question.question, conversation_id=conversation_id, sentence_alignment=align
    )
    latency = time.perf_counter() - start

    expects_rag = question.expected_route in {"rag", "mixed"}
    expects_tool = question.expected_route in {"tool", "mixed"}
    citations = result.get("citations", [])
    citation_valid = (
        all(c.get("document_id") and c.get("chunk_id") for c in citations)
        if citations
        else False
    )

    qa_result = result.get("qa_result") or {}
    unsupported = qa_result.get("unsupported_sentences")

    return {
        "id": question.id,
        "category": question.category,
        "expected_route": question.expected_route,
        "actual_route": result["route"],
        "route_decision": result.get("route_decision", ""),
        "route_ok": result["route"] == question.expected_route,
        "tool_called": bool(result.get("tool_calls")) if expects_tool else None,
        "retrieval_hit": bool(citations) if expects_rag else None,
        "citation_valid": citation_valid if expects_rag else None,
        "answer_ok": _keyword_hit(result["final_answer"], question.answer_keywords),
        "qa_passed": result.get("qa_passed", False),
        "degraded": result.get("degraded", False),
        "retry_count": result.get("retry_count", 0),
        "unsupported_count": len(unsupported) if unsupported is not None else None,
        "citation_aligned": (
            len(unsupported) == 0 if unsupported is not None else None
        ),
        "latency": latency,
        "profile": result.get("profile", {}),
        "answer": result["final_answer"],
    }


def _rate(items: list[dict], key: str) -> float:
    if not items:
        return 1.0
    return sum(1 for d in items if d[key]) / len(items)


def _rate_or_none(items: list[dict], key: str) -> float | None:
    """样本为空时返回 None（表示未采集，而非 100%）。"""
    if not items:
        return None
    return sum(1 for d in items if d[key]) / len(items)


def evaluate_dataset(
    questions: list[TestQuestion] | None = None,
    limit: int | None = None,
    conversation_prefix: str = "eval",
    align: bool = False,
    use_cache: bool = False,
) -> dict:
    """对测试集运行评估，返回聚合指标与逐题明细。

    use_cache=False（默认）会先清空回答缓存，保证每题真实走一遍链路，
    这样 Latency 指标才有参考意义（否则重复问题会 0s 命中缓存）。
    """
    if not use_cache:
        cache.clear()

    questions = questions or load_test_questions()
    if limit:
        questions = questions[:limit]

    details = []
    total = len(questions)
    for i, q in enumerate(questions, start=1):
        d = evaluate_question(
            q, conversation_id=f"{conversation_prefix}-{q.id}", align=align
        )
        details.append(d)
        logger.info(
            "[%d/%d] %-16s route=%s(期望 %s) qa=%s retry=%d answer=%s %.1fs",
            i,
            total,
            q.id,
            d["actual_route"],
            q.expected_route,
            d["qa_passed"],
            d["retry_count"],
            d["answer_ok"],
            d["latency"],
        )

    n = len(details) or 1
    rag_items = [d for d in details if d["retrieval_hit"] is not None]
    tool_items = [d for d in details if d["tool_called"] is not None]
    aligned_items = [d for d in details if d["citation_aligned"] is not None]
    latencies = [d["latency"] for d in details]

    # 分阶段耗时聚合（avg / P95），以及路由决策路径分布
    stage_keys: set[str] = set()
    for d in details:
        stage_keys.update((d.get("profile") or {}).keys())
    stage_avg: dict[str, float] = {}
    for key in sorted(stage_keys):
        values = [
            d["profile"][key] for d in details if key in (d.get("profile") or {})
        ]
        if values:
            stage_avg[f"{key}_avg"] = round(statistics.mean(values), 1)

    decision_counts: dict[str, int] = {}
    for d in details:
        decision = d.get("route_decision") or "unknown"
        decision_counts[decision] = decision_counts.get(decision, 0) + 1

    return {
        "total": len(details),
        "routing_accuracy": _rate(details, "route_ok"),
        "tool_calling_accuracy": _rate(tool_items, "tool_called"),
        "retrieval_hit_rate": _rate(rag_items, "retrieval_hit"),
        "citation_accuracy": _rate(rag_items, "citation_valid"),
        "citation_alignment_rate": _rate_or_none(aligned_items, "citation_aligned"),
        "answer_accuracy": _rate(details, "answer_ok"),
        "qa_pass_rate": _rate(details, "qa_passed"),
        "degrade_rate": _rate(details, "degraded"),
        "retry_rate": sum(d["retry_count"] for d in details) / n,
        "latency_avg": (sum(latencies) / n) if latencies else 0.0,
        "latency_max": max(latencies) if latencies else 0.0,
        "latency_p95": _p95(latencies),
        "route_decisions": decision_counts,
        "stage_avg_ms": stage_avg,
        "details": details,
    }
