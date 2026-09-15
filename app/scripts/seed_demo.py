"""生成演示用对话（覆盖 RAG / Tool / Mixed / 多轮 / 异常 / 无答案）。

用法：
    uv run python -m app.scripts.seed_demo                  # 清空已有会话后生成
    uv run python -m app.scripts.seed_demo --keep-existing  # 保留已有会话

生成后，对话页「历史会话」中会有一条带标题的演示会话，依次包含：

    1. RAG      —— 知识库政策检索（带溯源）
    2. 多轮      —— 指代消解（"那…" → Query Rewrite）
    3. Tool     —— 商品查询（LLM Tool Calling）
    4. Mixed    —— 业务数据 + 知识政策（Tool + RAG）
    5. 异常      —— 订单不存在（Tool 错误处理）
    6. RAG 无答案 —— 知识库无依据时不编造
"""

from __future__ import annotations

import argparse
import time

from app import cache, memory
from app.agents.main_graph import run_main_graph

DEMO_CONVERSATION_ID = "demo-xingchen"
DEMO_TITLE = "演示：退货政策 / 商品与订单查询 / 多轮追问"

DEMO_TURNS: list[tuple[str, str]] = [
    ("你好，想了解一下你们的退货政策", "RAG · 知识库政策检索 + 溯源"),
    ("那退货运费谁承担？", "多轮 · 指代消解 / Query Rewrite"),
    ("K87 机械键盘现在多少钱？", "Tool · 商品查询（Tool Calling）"),
    ("订单 ORD20260002 显示待发货，你们什么时候发货？", "Mixed · 业务数据 + 知识政策"),
    ("帮我查一下订单 ORD99999999 的状态", "异常 · 订单不存在"),
    ("你们商城有实体门店吗？", "RAG 无答案 · 不编造事实"),
]


def _clear_all_conversations() -> None:
    ids = [item["conversation_id"] for item in memory.list_conversations()]
    if ids:
        memory.delete_conversations(ids)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成演示对话")
    parser.add_argument("--keep-existing", action="store_true", help="保留已有会话")
    args = parser.parse_args()

    if args.keep_existing:
        memory.delete_conversations([DEMO_CONVERSATION_ID])
    else:
        _clear_all_conversations()
    cache.clear()

    total = len(DEMO_TURNS)
    for index, (question, note) in enumerate(DEMO_TURNS, start=1):
        start = time.perf_counter()
        result = run_main_graph(question, conversation_id=DEMO_CONVERSATION_ID)
        elapsed = time.perf_counter() - start
        answer = str(result["final_answer"]).replace("\n", " ")
        print(f"[{index}/{total}] {note}")
        print(f"  问: {question}")
        print(
            f"  路由={result['route']}  耗时={elapsed:.1f}s  "
            f"溯源={len(result['citations'])}  "
            f"QA={'通过' if result['qa_passed'] else '未通过'}"
        )
        print(f"  答: {answer[:100]}…")

    memory.rename_conversation(DEMO_CONVERSATION_ID, DEMO_TITLE)
    print(f"\n演示会话已生成：{DEMO_CONVERSATION_ID}")
    print(f"标题：{DEMO_TITLE}")


if __name__ == "__main__":
    main()
