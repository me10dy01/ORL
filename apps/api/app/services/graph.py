"""LangGraph 编排：Triage（分流）→ Resolve（解决）| TicketFill（建单预填）。

把「先判断要不要转人工，再决定走机器人还是建工单」的控制流显式化成图：
节点是处理步骤，边带条件，状态（ChatState）在节点间流动。
面试考点：为什么用 LangGraph 而不是 if-else、节点如何只更新自己负责的键。

注意：这只服务非流式 /api/chat；流式路径为了延迟在 routes.py 里手写串联同样逻辑
（两条路径并存是已知技术债，计划让 LangGraph 节点直接产出事件流来统一）。
"""

from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.services.bot import (
    ANSWER_FALLBACK,
    consecutive_refusals,
    handoff_priority,
    is_refusal,
    parse_prefill,
    prefill_prompt,
)
from app.services.tool_loop import answer_with_tools


class ChatState(TypedDict, total=False):
    """图状态：total=False 表示每个节点只需返回自己更新的键，不必返回全量。"""

    message: str
    history: list[dict]
    rewritten: str
    slots: dict
    user_id: str
    handoff: bool  # 是否转人工——triage/resolve 都可能置位
    priority: str  # 工单优先级：high（投诉类）/ normal
    reply: str
    refused: bool  # 本轮回复是否为拒答话术
    lookup: dict  # 订单查询结果快照（转人工时随工单带走）
    category: str
    summary: str


def triage_update(message: str) -> dict:
    """关键词分流。被 graph 节点和流式路由共用——两条路径的转人工判定必须一致。"""
    priority = handoff_priority(message)
    if priority:
        return {"handoff": True, "priority": priority}
    return {"handoff": False}


def lookup_payload(result) -> dict:
    """把订单查询结果转成可入图状态/入工单事件的纯字典（去掉 ORM 对象）。"""
    if result is None:
        return {}
    return {
        "text": result.text,
        "order_id": result.order_id,
        "candidates": list(result.candidates or []),
    }


def fill_ticket(message: str, complete) -> tuple[str, str]:
    """让 LLM 预填工单分类和摘要；解析失败兜底 general+用户原话——永不信任模型格式。"""
    try:
        return parse_prefill(complete(prefill_prompt(message)), message)
    except Exception:
        return "general", message


def needs_refusal_handoff(history: list[dict], reply: str) -> bool:
    """连续两次拒答（含本轮）→ 转人工。把「答不上来」升级成业务动作。"""
    previous = [item.get("content", "") for item in history if item.get("role") == "assistant"]
    return consecutive_refusals(previous, reply) >= 2


def resolve_turn(
    complete,
    *,
    db,
    embed,
    user_id: str,
    message: str,
    slots: dict,
    rewritten: str,
    history: list[dict],
) -> dict:
    """Resolve 节点核心：跑工具循环，并判断结果是否触发拒答转人工。"""
    try:
        reply, lookup = answer_with_tools(
            complete,
            db=db,
            embed=embed,
            user_id=user_id,
            message=message,
            slots=slots,
            rewritten=rewritten,
            history=history,
        )
    except Exception:
        reply, lookup = ANSWER_FALLBACK, None
    handoff = needs_refusal_handoff(history, reply)
    update = {
        "reply": reply,
        "refused": is_refusal(reply),
        "lookup": lookup_payload(lookup),
        "handoff": handoff,
    }
    if handoff:
        update["priority"] = "normal"
    return update


def build_chat_graph(complete, *, db, embed):
    """编译三节点图。条件边读 state["handoff"] 决定下一站。

    拓扑：
        triage ──handoff──> ticket_fill ──> END
           └──────无──────> resolve ──handoff(拒答)──> ticket_fill ──> END
                                  └────────正常──────> END
    """

    def triage(state: ChatState) -> dict:
        return triage_update(state["message"])

    def resolve(state: ChatState) -> dict:
        return resolve_turn(
            complete,
            db=db,
            embed=embed,
            user_id=state["user_id"],
            message=state["message"],
            slots=state.get("slots") or {},
            rewritten=state.get("rewritten") or state["message"],
            history=state.get("history") or [],
        )

    def ticket_fill(state: ChatState) -> dict:
        category, summary = fill_ticket(state["message"], complete)
        return {"category": category, "summary": summary}

    graph = StateGraph(ChatState)
    graph.add_node("triage", triage)
    graph.add_node("resolve", resolve)
    graph.add_node("ticket_fill", ticket_fill)
    graph.set_entry_point("triage")
    graph.add_conditional_edges(
        "triage",
        lambda state: "ticket_fill" if state.get("handoff") else "resolve",
        {"ticket_fill": "ticket_fill", "resolve": "resolve"},
    )
    graph.add_conditional_edges(
        "resolve",
        lambda state: "ticket_fill" if state.get("handoff") else END,
        {"ticket_fill": "ticket_fill", END: END},
    )
    graph.add_edge("ticket_fill", END)
    return graph.compile()
