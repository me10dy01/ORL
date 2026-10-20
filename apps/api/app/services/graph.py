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
    message: str
    history: list[dict]
    rewritten: str
    slots: dict
    user_id: str
    handoff: bool
    priority: str
    reply: str
    refused: bool
    lookup: dict
    category: str
    summary: str


def triage_update(message: str) -> dict:
    """Keyword gate shared by the graph and the streaming route."""
    priority = handoff_priority(message)
    if priority:
        return {"handoff": True, "priority": priority}
    return {"handoff": False}


def lookup_payload(result) -> dict:
    """Plain lookup fields safe to keep in graph state."""
    if result is None:
        return {}
    return {
        "text": result.text,
        "order_id": result.order_id,
        "candidates": list(result.candidates or []),
    }


def fill_ticket(message: str, complete) -> tuple[str, str]:
    """Ask the model for category and summary. Failure keeps a safe default."""
    try:
        return parse_prefill(complete(prefill_prompt(message)), message)
    except Exception:
        return "general", message


def needs_refusal_handoff(history: list[dict], reply: str) -> bool:
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
    """Run the tool loop and record whether the server-owned reply is a refusal."""
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
    """Compile Triage, Resolve, and TicketFill."""

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
