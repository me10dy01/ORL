"""RAG 检索效果评估（Phase 10）：Baseline vs Optimized 对比。

指标：
- Hit@N：top-N 结果中是否命中预期关键词
- MRR：命中片段的倒数排名平均

具体参数（top_k / top_n）通过测试集验证，不预设"必然最好"。
"""

from __future__ import annotations

from app.rag.pipeline import retrieve

# RAG 类测试问题（问题, 预期关键词）
RAG_EVAL_CASES = [
    ("星辰数码商城的退货政策是什么？", ["无理由退货"]),
    ("K87 键盘支持退货吗？", ["K87"]),
    ("退货时运费由谁承担？", ["运费"]),
    ("商城下单后多久发货？", ["24 小时"]),
    ("耳机的保修期是多久？", ["2 年"]),
]


def _hit(results: list[dict], keywords: list[str]) -> bool:
    joined = " ".join(r["content"] for r in results)
    return any(k in joined for k in keywords)


def _reciprocal_rank(results: list[dict], keywords: list[str]) -> float:
    for i, r in enumerate(results, start=1):
        if any(k in r["content"] for k in keywords):
            return 1.0 / i
    return 0.0


def evaluate(
    cases: list[tuple[str, list[str]]] | None = None,
    top_n: int = 2,
    top_k: int = 6,
) -> dict:
    """对测试集运行 Baseline 与 Optimized 检索，返回对比指标。"""
    cases = cases or RAG_EVAL_CASES
    base_hits = opt_hits = 0
    base_rr = opt_rr = 0.0
    details: list[dict] = []

    for query, keywords in cases:
        base = retrieve(query, top_k=top_n, rerank=False)
        opt = retrieve(
            query,
            top_k=top_k,
            top_n=top_n,
            rerank=True,
        )
        bh = _hit(base, keywords)
        oh = _hit(opt, keywords)
        brr = _reciprocal_rank(base, keywords)
        orr = _reciprocal_rank(opt, keywords)
        base_hits += bh
        opt_hits += oh
        base_rr += brr
        opt_rr += orr
        details.append(
            {
                "query": query,
                "baseline_hit": bh,
                "optimized_hit": oh,
                "baseline_rr": brr,
                "optimized_rr": orr,
            }
        )

    n = len(cases) or 1
    return {
        "baseline": {"hit": base_hits / n, "mrr": base_rr / n},
        "optimized": {"hit": opt_hits / n, "mrr": opt_rr / n},
        "details": details,
    }
