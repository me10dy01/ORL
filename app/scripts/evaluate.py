"""运行 RAG 检索评估：Baseline vs Optimized。

用法：
    uv run python -m app.scripts.evaluate
"""

import json

from app.rag.evaluation import evaluate

if __name__ == "__main__":
    report = evaluate(top_n=2, top_k=6)
    print(json.dumps(report, ensure_ascii=False, indent=2))
