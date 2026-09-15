"""Query Rewrite（Phase 09）。

多轮对话中指代消解与补全，输出独立完整查询。
原则（AGENTS.md 第 15 条）：不改变用户原始意图。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app import profiling
from app.llm import get_chat_model

SYSTEM_PROMPT = (
    "你是查询改写助手。根据对话历史，把用户当前问题改写为一个独立完整的查询，消除指代和省略（如“那第二种呢”“运费谁承担”等）。\n"
    "要求：\n"
    "1. 保留并补全历史中提到的具体实体（如商品名 K87、订单号、用户ID等），把指代还原为具体实体。\n"
    "2. 保持用户原始意图，不要添加新信息，也不要回答问题。\n"
    "3. 如果当前问题已经完整独立，直接原样输出。\n"
    "只输出改写后的查询，不要输出任何解释。\n"
    "示例1：历史“K87 支持退货吗？”，当前问题“那运费谁承担？” → “星辰数码商城 K87 退货时的运费由谁承担？”\n"
    "示例2：历史“支持两种退货方式：1. 七天无理由退货；2. 质量问题退货”，当前问题“那第二种呢？” "
    "→ “质量问题退货的规则是什么？”\n"
)


def rewrite_query(history: list[dict], current_query: str) -> str:
    """结合历史改写当前查询。无历史时原样返回。"""
    if not history:
        return current_query
    history_text = "\n".join(
        f"{m.get('role', '?')}: {m.get('content', '')}" for m in history
    )
    prompt = (
        f"对话历史：\n{history_text}\n\n用户当前问题：{current_query}\n\n改写后的查询："
    )
    llm = get_chat_model()
    with profiling.stage("rewrite"):
        profiling.mark("llm_calls")
        result = llm.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
        )
        content = result.content
        rewritten = (content if isinstance(content, str) else str(content)).strip()
        return rewritten or current_query
