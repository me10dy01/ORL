from collections.abc import Iterator

from app.llm import ModelTurn, ToolCall
from app.mcp_client import call_tool, use_request_tools
from app.mcp_server import LookupOrdersResult
from app.services.bot import ANSWER_FALLBACK, answer_from_tools_prompt, answer_prompt, tool_choice_prompt

MAX_TOOL_ROUNDS = 3
TOOL_STATUS = {
    "search_knowledge": "正在检索知识",
    "lookup_orders": "正在查订单",
    "lookup_stock": "正在查库存",
}
MODEL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search store policy passages for the user's question.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_orders",
            "description": "Look up the signed-in user's orders. Do not pass a user id or order id.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_stock",
            "description": "Look up public product stock by name or product id. Do not pass a user id.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


def answer_with_tools(
    complete,
    *,
    db,
    embed,
    user_id: str,
    message: str,
    slots: dict,
    rewritten: str,
    history: list[dict],
) -> tuple[str, LookupOrdersResult | None]:
    """Let the model call tools, at most three rounds, and return the final text."""
    lookup_box: dict[str, LookupOrdersResult | None] = {"result": None}
    parts: list[str] = []
    for event in iter_answer_events(
        complete,
        db=db,
        embed=embed,
        user_id=user_id,
        message=message,
        slots=slots,
        rewritten=rewritten,
        history=history,
        lookup_box=lookup_box,
    ):
        if event["type"] == "reset":
            parts.clear()
        elif event["type"] == "token":
            parts.append(event["content"])
    return "".join(parts).strip() or ANSWER_FALLBACK, lookup_box["result"]


def iter_answer_events(
    complete,
    *,
    db,
    embed,
    user_id: str,
    message: str,
    slots: dict,
    rewritten: str,
    history: list[dict],
    lookup_box: dict,
) -> Iterator[dict]:
    """Yield status, token, and reset events in model order."""
    if not hasattr(complete, "with_tools") and not hasattr(complete, "with_stream"):
        yield {"type": "token", "content": complete(answer_prompt(rewritten, history))}
        return

    from app.services.bot import profile_line
    from app.services.profile import get_profile

    profile = get_profile(db, user_id)
    messages = [
        {"role": "user", "content": tool_choice_prompt(rewritten, history, profile_line(profile))}
    ]
    answered = False
    for _ in range(MAX_TOOL_ROUNDS):
        parts: list[str] = []
        calls: list[ToolCall] = []
        emitted = False
        try:
            for chunk in _chunks(complete, messages, MODEL_TOOLS):
                if not isinstance(chunk, ModelTurn):
                    text = str(chunk)
                    if text and not calls:
                        parts.append(text)
                        emitted = True
                        yield {"type": "token", "content": text}
                    continue
                if chunk.tool_calls:
                    calls = list(chunk.tool_calls)
                if chunk.content and not calls:
                    parts.append(chunk.content)
                    emitted = True
                    yield {"type": "token", "content": chunk.content}
        except Exception:
            if emitted:
                yield {"type": "reset"}
            yield {"type": "token", "content": ANSWER_FALLBACK}
            return
        if calls:
            if emitted:
                yield {"type": "reset"}
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": call.name, "arguments": call.arguments}}
                        for call in calls
                    ],
                }
            )
            for call in calls:
                yield {"type": "status", "message": TOOL_STATUS.get(call.name, "正在查询")}
                text, lookup_box["result"] = _execute(
                    call,
                    db,
                    embed,
                    user_id,
                    message,
                    slots,
                    rewritten,
                    lookup_box["result"],
                )
                messages.append({"role": "tool", "content": text, "name": call.name})
            messages.append({"role": "user", "content": answer_from_tools_prompt()})
            continue
        answered = True
        if not parts:
            yield {"type": "token", "content": ANSWER_FALLBACK}
        return
    if not answered:
        yield {"type": "token", "content": ANSWER_FALLBACK}


def _chunks(complete, messages: list[dict], tools: list[dict]):
    if hasattr(complete, "with_stream"):
        yield from complete.with_stream(messages, tools)
        return
    yield complete.with_tools(messages, tools)


def _execute(
    call: ToolCall,
    db,
    embed,
    user_id: str,
    message: str,
    slots: dict,
    rewritten: str,
    lookup_result: LookupOrdersResult | None,
) -> tuple[str, LookupOrdersResult | None]:
    with use_request_tools(db, embed, user_id=user_id, message=message, slots=slots):
        if call.name == "search_knowledge":
            query = call.arguments.get("query") or rewritten
            found = call_tool("search_knowledge", {"query": query}) or ""
            return str(found), lookup_result
        if call.name == "lookup_orders":
            result = call_tool("lookup_orders", {})
            return result.text, result
        if call.name == "lookup_stock":
            query = str(call.arguments.get("query") or "")
            try:
                found = call_tool("lookup_stock", {"query": query}) or ""
            except Exception:
                found = "未找到"
            return str(found), lookup_result
    return "", lookup_result
