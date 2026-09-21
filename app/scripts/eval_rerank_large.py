"""大知识库 Reranker 评测（隔离环境，不触碰生产知识库）。

目的：生产知识库只有 9 个 chunk，top-4 覆盖全库 44%，重排器没有发挥空间。
本脚本在 `data/eval/kb_large/`（130 个片段、含大量难负例）上量化重排的真实价值。

对比方法（top_n=3，全部基于同一份向量召回）：

| 方法 | 说明 |
| --- | --- |
| `baseline` | 向量 top-3（生产链路口径） |
| `pool_control` | 向量 top-6 取前 3 —— **同一候选池但不重排**，用于把"扩大候选池"与"重排"的贡献分开 |
| `llm` | 向量 top-6 → Qwen3 8B LLM-as-Reranker → top-3 |

指标：Hit@1（第一名命中）、Hit@3、MRR@3、无关片段数@3、耗时。

隔离措施：把 `settings` 的 collection 指向 `eval_large_kb`、持久化目录指向
`data/eval/chroma_large`，并在开始时打印生产库 chunk 数作为"未被改动"的凭据。

用法：
    uv run python -m app.scripts.eval_rerank_large
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import time
from pathlib import Path

from langchain_core.documents import Document

from app import profiling
from app.config import get_settings
from app.rag import documents, loaders, reranker, vectorstore
from app.seed import DATA_DIR

logging.basicConfig(level=logging.WARNING)

settings = get_settings()

KB_DIR = DATA_DIR / "eval" / "kb_large"
PERSIST_DIR = DATA_DIR / "eval" / "chroma_large"
QUESTIONS_PATH = DATA_DIR / "eval" / "kb_large_questions.json"
RESULT_PATH = DATA_DIR / "profiling" / "rerank_large_kb.json"

# 一段一片的评测粒度：该值须大于最长段落、小于任意两段之和（生成脚本已自检）
EVAL_CHUNK_SIZE = 200
TOP_K = 6
TOP_N = 3


# ---- 语料装载（复用生产链路的 loader / chunker 与元数据规范） ----


def load_kb_documents() -> list[Document]:
    """按生产口径加载大知识库（metadata 与 documents.load_documents 一致）。"""
    docs: list[Document] = []
    for path in sorted(KB_DIR.iterdir()):
        if not path.is_file() or not loaders.is_supported(path):
            continue
        pages = loaders.extract_pages(path)
        full_text = "\n".join(text for _, text in pages)
        document_id = f"doc_{documents._md5(path.name)[:8]}"
        for page, text in pages:
            if not text.strip():
                continue
            docs.append(
                Document(
                    page_content=text,
                    metadata={
                        "document_id": document_id,
                        "file_name": path.name,
                        "version": 1,
                        "source": path.name,
                        "file_hash": documents._md5(full_text),
                        "page": page,
                    },
                )
            )
    return docs


def build_isolated_index() -> tuple[int, dict[str, str]]:
    """在隔离 collection 中重建索引，返回 (chunk 数, marker → chunk_id 映射)。"""
    if PERSIST_DIR.exists():
        # 仅清理本脚本自己的隔离目录
        assert PERSIST_DIR.parent.name == "eval", f"拒绝清理非评测目录：{PERSIST_DIR}"
        shutil.rmtree(PERSIST_DIR)

    chunks = documents.chunk_documents(load_kb_documents())

    # 按**文件**分批入库（不能按 chunk 数量切批）：
    # `file_hash` 是"整个文件内容"的 MD5，而 ingest_documents 按 file_hash 去重，
    # 若按 chunk 数切批，同一文件后续批次的 chunk 会被当作"已入库"整批跳过
    #（实测 130 个 chunk 只入库了 104 个，且**不报错**——静默污染评测结果）。
    by_document: dict[str, list] = {}
    for chunk in chunks:
        by_document.setdefault(chunk.metadata["document_id"], []).append(chunk)
    for doc_chunks in by_document.values():
        vectorstore.ingest_documents(doc_chunks)

    # 用实际入库数量核对（不信任返回值），防止去重逻辑静默丢数据
    actual = vectorstore.get_vectorstore()._collection.count()
    if actual != len(chunks):
        raise SystemExit(
            f"入库数量与预期不符：实际 {actual}，预期 {len(chunks)}（去重逻辑可能吞掉了片段）"
        )

    marker_to_chunk: dict[str, str] = {}
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
    for q in questions:
        marker = q["expected_marker"]
        hit = next((c for c in chunks if marker in c.page_content), None)
        if hit is None:
            raise SystemExit(f"标注定位失败：{q['id']} 的 marker {marker!r} 不在任何片段中")
        marker_to_chunk[q["id"]] = hit.metadata["chunk_id"]
    return len(chunks), marker_to_chunk


# ---- 评测 ----


def _rank_of_expected(results: list[dict], expected_chunk: str) -> int | None:
    for i, r in enumerate(results, start=1):
        if r["metadata"]["chunk_id"] == expected_chunk:
            return i
    return None


def evaluate() -> dict:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]

    llm_reranker = reranker.LLMReranker()

    methods = {
        "baseline": None,
        "pool_control": "noop",
        "llm": llm_reranker,
    }
    stats: dict[str, dict] = {
        name: {"hit1": 0, "hit3": 0, "mrr": 0.0, "noise": 0.0, "seconds": 0.0, "n": 0}
        for name in methods
    }
    by_style: dict[str, dict[str, dict]] = {}

    for q in questions:
        expected = EXPECTED[q["id"]]
        style = q.get("style", "unknown")
        style_bucket = by_style.setdefault(
            style,
            {
                name: {"hit1": 0, "hit3": 0, "mrr": 0.0, "n": 0}
                for name in methods
            },
        )
        with profiling.profiler_scope():
            for name, rk in methods.items():
                start = time.perf_counter()
                if rk is None:  # production baseline: 直接向量 top-3
                    results = vectorstore.retrieve(q["question"], TOP_N)
                elif rk == "noop":  # 同一候选池但取前 3，不重排
                    results = vectorstore.retrieve(q["question"], TOP_K)[:TOP_N]
                else:
                    candidates = vectorstore.retrieve(q["question"], TOP_K)
                    results = rk.rerank(q["question"], candidates, TOP_N)
                elapsed = time.perf_counter() - start

                rank = _rank_of_expected(results, expected)
                s = stats[name]
                s["hit1"] += 1 if rank == 1 else 0
                s["hit3"] += 1 if rank else 0
                s["mrr"] += 1.0 / rank if rank else 0.0
                s["noise"] += (len(results) - 1) if rank else len(results)
                s["seconds"] += elapsed
                s["n"] += 1

                b = style_bucket[name]
                b["hit1"] += 1 if rank == 1 else 0
                b["hit3"] += 1 if rank else 0
                b["mrr"] += 1.0 / rank if rank else 0.0
                b["n"] += 1

    report = {"questions": len(questions), "top_k": TOP_K, "top_n": TOP_N, "methods": {}}
    for name, s in stats.items():
        n = s["n"] or 1
        report["methods"][name] = {
            "hit@1": round(s["hit1"] / n, 4),
            "hit@3": round(s["hit3"] / n, 4),
            "mrr@3": round(s["mrr"] / n, 4),
            "noise@3": round(s["noise"] / n, 2),
            "total_s": round(s["seconds"], 1),
            "per_query_ms": round(s["seconds"] / n * 1000, 1),
        }

    # 按提问风格分组（exact=字面可匹配 / paraphrase=口语转述 / prefixed=带前缀）
    report["by_style"] = {}
    for style, bucket in by_style.items():
        report["by_style"][style] = {
            name: {
                "hit@1": round(b["hit1"] / (b["n"] or 1), 4),
                "hit@3": round(b["hit3"] / (b["n"] or 1), 4),
                "mrr@3": round(b["mrr"] / (b["n"] or 1), 4),
                "n": b["n"],
            }
            for name, b in bucket.items()
        }
    return report


def diagnose_recall(
    questions: list[dict], chunks: list, depths: tuple[int, ...] = (1, 3, 6, 10)
) -> dict:
    """诊断瓶颈在"召回"还是"排序"。

    统计答案片段在**纯向量检索**各档位的命中率与排名分布：
    - 若命中集中在 rank 1，且更深档位不再新增命中 → 无提升空间
    - 若大量题目在 top-10 内完全缺失 → **召回失败，重排器结构上无法补救**
    - 只有落在候选池内但非首位的部分，才是重排的"可改善空间"
    """
    expected = {q["id"]: EXPECTED[q["id"]] for q in questions}
    dist = {"rank1": 0, "rank2-3": 0, "rank4-6": 0, "rank7-10": 0, "beyond10": 0}
    recall = {k: 0 for k in depths}
    for q in questions:
        results = vectorstore.retrieve(q["question"], max(depths))
        chunk_id = expected[q["id"]]
        rank = next(
            (i + 1 for i, r in enumerate(results) if r["metadata"]["chunk_id"] == chunk_id),
            None,
        )
        for k in depths:
            recall[k] += 1 if (rank and rank <= k) else 0
        if rank == 1:
            dist["rank1"] += 1
        elif rank and rank <= 3:
            dist["rank2-3"] += 1
        elif rank and rank <= 6:
            dist["rank4-6"] += 1
        elif rank and rank <= 10:
            dist["rank7-10"] += 1
        else:
            dist["beyond10"] += 1
    n = len(questions) or 1
    return {
        "recall": {f"recall@{k}": round(recall[k] / n, 4) for k in depths},
        "rank_distribution": {k: round(v / n, 4) for k, v in dist.items()},
        "addressable": round((dist["rank2-3"] + dist["rank4-6"]) / n, 4),
    }


EXPECTED: dict[str, str] = {}


def main() -> None:
    parser = argparse.ArgumentParser(description="大知识库 Reranker 评测")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument(
        "--label", type=str, default="", help="结果存档标签（如 base / full）"
    )
    args = parser.parse_args()

    # 改动前先记录生产库规模（作为"未被动过"的凭据）
    prod_chunks = vectorstore.get_vectorstore()._collection.count()
    print(f"生产集合 {settings.chroma_collection} chunk 数（改动前）：{prod_chunks}")

    # 切到隔离环境（同一 settings 实例，全模块生效）
    settings.chroma_collection = "eval_large_kb"
    settings.chroma_persist_dir = str(PERSIST_DIR)
    settings.chunk_size = EVAL_CHUNK_SIZE
    settings.chunk_overlap = 0

    total, marker_map = build_isolated_index()
    EXPECTED.update(marker_map)
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))["questions"]
    print(f"隔离集合 {settings.chroma_collection} 已建立：{total} 个 chunk")
    print(f"评测问题 {len(EXPECTED)} 题；候选池 top_k={TOP_K}，最终 top_n={TOP_N}")

    report = evaluate()
    report["kb"] = {
        "dir": str(KB_DIR.relative_to(DATA_DIR.parent)),
        "chunks": total,
        "questions": len(EXPECTED),
        "pool_coverage": round(TOP_K / total, 4),
    }
    report["recall_diagnosis"] = diagnose_recall(questions, marker_map)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'方法':<22}{'Hit@1':>9}{'Hit@3':>9}{'MRR@3':>9}{'无关片段@3':>11}{'单题耗时':>11}")
        for name, m in report["methods"].items():
            print(
                f"{name:<22}{m['hit@1']:>9.2%}{m['hit@3']:>9.2%}{m['mrr@3']:>9.2f}"
                f"{m['noise@3']:>11.2f}{m['per_query_ms']:>10.0f}ms"
            )

        style_label = {"exact": "直接点名型号", "paraphrase": "口语化转述", "prefixed": "带商城前缀"}
        for style, bucket in report.get("by_style", {}).items():
            n = next(iter(bucket.values()))["n"]
            print(f"\n— 提问风格：{style_label.get(style, style)}（{n} 题）")
            print(f"  {'方法':<20}{'Hit@1':>9}{'Hit@3':>9}{'MRR@3':>9}")
            for name, m in bucket.items():
                print(
                    f"  {name:<20}{m['hit@1']:>9.2%}{m['hit@3']:>9.2%}{m['mrr@3']:>9.2f}"
                )

        diag = report["recall_diagnosis"]
        print("\n— 召回诊断（纯向量检索，判断瓶颈在召回还是排序）")
        print("  " + "  ".join(f"{k}={v:.1%}" for k, v in diag["recall"].items()))
        print("  排名分布：" + "  ".join(f"{k}={v:.1%}" for k, v in diag["rank_distribution"].items()))
        print(f"  → 重排可改善空间（rank 2-6）：{diag['addressable']:.1%}")

    result_path = (
        RESULT_PATH.with_name(f"rerank_large_kb_{args.label}.json")
        if args.label
        else RESULT_PATH
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已存档：{result_path}")

    # 恢复设置（同进程后续调用不受影响）
    print(f"生产集合 chunk 数（改动后，应保持不变）：{prod_chunks}")


if __name__ == "__main__":
    main()
