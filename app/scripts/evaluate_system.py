"""运行系统级评估（Phase 15）。

用法：
    uv run python -m app.scripts.evaluate_system                       # 全量测试集
    uv run python -m app.scripts.evaluate_system --limit 5             # 前 5 题
    uv run python -m app.scripts.evaluate_system --ids q_rag_001,q_tool_001
    uv run python -m app.scripts.evaluate_system --json                # 完整 JSON
    uv run python -m app.scripts.evaluate_system --align               # 额外跑逐句归因
"""

from __future__ import annotations

import argparse
import json
import logging

from app.evaluation import evaluate_dataset
from app.rag import build_index
from app.seed import load_test_questions


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="系统级评估")
    parser.add_argument("--limit", type=int, default=None, help="仅评估前 N 题")
    parser.add_argument("--ids", type=str, default=None, help="逗号分隔的问题ID")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument(
        "--align",
        action="store_true",
        help="开启 Citation 逐句归因（较慢，会额外调用 LLM）",
    )
    args = parser.parse_args()

    questions = None
    if args.ids:
        wanted = {x.strip() for x in args.ids.split(",") if x.strip()}
        questions = [q for q in load_test_questions() if q.id in wanted]

    build_index()  # 确保知识库已入库
    report = evaluate_dataset(questions=questions, limit=args.limit, align=args.align)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print()
    print(f"样本数:                  {report['total']}")
    print(f"Routing Accuracy:        {report['routing_accuracy']:.2%}")
    print(f"Tool Calling Accuracy:   {report['tool_calling_accuracy']:.2%}")
    print(f"Retrieval Hit Rate:      {report['retrieval_hit_rate']:.2%}")
    print(f"Citation Accuracy:       {report['citation_accuracy']:.2%}")
    align_rate = report.get("citation_alignment_rate")
    align_txt = (
        "未采集（加 --align 开启）" if align_rate is None else f"{align_rate:.2%}"
    )
    print(f"Citation Alignment Rate: {align_txt}")
    print(f"Answer Accuracy:         {report['answer_accuracy']:.2%}")
    print(f"QA Pass Rate:            {report['qa_pass_rate']:.2%}")
    print(f"Degrade Rate:            {report.get('degrade_rate', 0.0):.2%}")
    print(f"Retry Rate:              {report['retry_rate']:.2f}")
    print(
        f"Latency Avg / P95 / Max: {report['latency_avg']:.1f}s / "
        f"{report.get('latency_p95', 0.0):.1f}s / {report['latency_max']:.1f}s"
    )
    decisions = report.get("route_decisions") or {}
    if decisions:
        dist = "  ".join(f"{k}={v}" for k, v in sorted(decisions.items()))
        print(f"路由决策路径分布:        {dist}")
    stages = report.get("stage_avg_ms") or {}
    if stages:
        print("各阶段平均耗时（ms）:")
        for key, value in sorted(stages.items()):
            print(f"  {key:<26} {value:>9.1f}")


if __name__ == "__main__":
    main()
