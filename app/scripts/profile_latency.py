"""Latency Profiling 脚本（性能剖析）。

对代表性问题集运行完整主图，输出各阶段耗时（ms）与 LLM 调用次数，
用于在模型职责重构前后建立客观的性能对比基线。

用法：
    uv run python -m app.scripts.profile_latency                  # 默认 6 题子集
    uv run python -m app.scripts.profile_latency --label baseline # 指定存档标签
    uv run python -m app.scripts.profile_latency --ids q_rag_002  # 指定题目

默认清空回答缓存，保证每题真实走完整链路（Latency 才有参考意义）。
结果 JSON 存档到 data/profiling/<label>.json。
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from app import cache, memory
from app.agents.main_graph import run_main_graph
from app.config import get_settings
from app.profiling import ordered_stages
from app.seed import load_test_questions

settings = get_settings()

# 6 题代表子集：rag×2 / tool×2 / mixed×1 / multi_turn×1，覆盖全部路由类型
DEFAULT_IDS = [
    "q_rag_002",
    "q_rag_004",
    "q_tool_001",
    "q_tool_002",
    "q_mixed_001",
    "q_multi_001",
]


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="主图延迟剖析")
    parser.add_argument("--ids", type=str, default="", help="逗号分隔的题目 id")
    parser.add_argument("--label", type=str, default="profile", help="存档标签")
    parser.add_argument("--no-clear-cache", action="store_true", help="保留回答缓存")
    args = parser.parse_args()

    ids = [i.strip() for i in args.ids.split(",") if i.strip()] or DEFAULT_IDS
    questions = {q.id: q for q in load_test_questions()}
    missing = [i for i in ids if i not in questions]
    if missing:
        raise SystemExit(f"题目不存在: {missing}")

    if not args.no_clear_cache:
        cleared = cache.clear()
        print(f"已清空回答缓存（{cleared} 条）")

    rows: list[dict] = []
    for i, qid in enumerate(ids, start=1):
        q = questions[qid]
        conversation_id = f"prof-{q.id}"
        memory.delete_conversation(conversation_id)
        for prior in q.history:
            memory.add_message(conversation_id, "user", prior)

        print(f"\n[{i}/{len(ids)}] {q.id}（{q.category}）: {q.question}")
        result = run_main_graph(q.question, conversation_id=conversation_id)
        profile = result.get("profile", {})

        stage_items = ordered_stages(profile)
        stage_text = "  ".join(f"{name}={ms:.0f}ms" for name, ms in stage_items)
        print(f"    route={result['route']} qa={'✓' if result['qa_passed'] else '✗'} "
              f"llm_calls={profile.get('llm_calls_count', 0):.0f}")
        print(f"    {stage_text}  total={profile.get('total_ms', 0):.0f}ms")

        rows.append(
            {
                "id": q.id,
                "category": q.category,
                "question": q.question,
                "route": result["route"],
                "qa_passed": result["qa_passed"],
                "retry_count": result["retry_count"],
                "answer": result["final_answer"],
                "profile": profile,
            }
        )
        memory.delete_conversation(conversation_id)

    # ---- 聚合 ----
    totals = [r["profile"].get("total_ms", 0.0) for r in rows]
    agg: dict[str, float] = {
        "total_avg": statistics.mean(totals) if totals else 0.0,
        "total_p95": _p95(totals),
    }
    stage_names: set[str] = set()
    for r in rows:
        stage_names.update(r["profile"].keys())
    for key in sorted(stage_names):
        values = [r["profile"][key] for r in rows if key in r["profile"]]
        if values:
            agg[f"{key}_avg"] = round(statistics.mean(values), 1)

    print("\n===== 汇总 =====")
    for key in sorted(agg):
        print(f"{key:<24} {agg[key]:>10.1f}")

    out_dir = Path(settings.chroma_persist_dir).parent / "profiling"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.label}.json"
    payload = {
        "label": args.label,
        "ids": ids,
        "aggregate": agg,
        "rows": rows,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已存档: {out_path}")


if __name__ == "__main__":
    main()
