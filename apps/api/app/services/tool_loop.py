"""工具循环（tool loop）：LLM Agent 的核心执行引擎。

这是 ReAct 模式的实现：模型每轮可以输出文本 token 或发起工具调用（tool call），
工具结果回灌上下文后进入下一轮，直到模型给出纯文本回答或达到轮数上限。

面试考点：工具调用的完整流程、流式输出与工具调用的冲突（reset 语义）、
身份注入为什么走 contextvar 而不是工具参数。
"""

from collections.abc import Iterator

from app.llm import ModelTurn, ToolCall
from app.mcp_client import call_tool, use_request_tools
from app.mcp_server import LookupOrdersResult
from app.services.bot import ANSWER_FALLBACK, answer_from_tools_prompt, answer_prompt, tool_choice_prompt

# 工具循环硬上限：防模型陷入「调工具→又想调」的死循环，也限制单轮延迟。
MAX_TOOL_ROUNDS = 3

# 前端「正在查订单…」状态条用的文案映射，按工具名取。
TOOL_STATUS = {
    "search_knowledge": "正在检索知识",
    "lookup_orders": "正在查订单",
    "lookup_stock": "正在查库存",
}

# 发给模型的工具 JSON Schema（Function Calling 标准格式）。
# 关键安全设计：lookup_orders 的 parameters 为空对象——模型连 user_id 参数都看不到，
# 从协议层面杜绝「模型替用户指定身份」的注入通道（schema 里 description 也明确禁止传 id）。
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
    """非流式入口（/api/chat 用）：把事件流折叠成最终文本。

    为什么不直接跑循环而是复用 iter_answer_events？——保证流式与非流式
    两条路径的「reset 作废语义」完全一致：遇到 reset 就清空已收集的文本，
    最终只返回最后一轮的 token 拼接结果。
    """
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
    """流式入口（/api/chat/stream 用）：按模型产出顺序 yield 事件。

    事件协议（SSE 的灵魂，面试高频）：
      - token  : 正文片段
      - status : 工具调用状态条（「正在查订单…」）
      - reset  : 作废本轮已发出的所有 token——模型先吐了字又决定调工具时发出，
                 前端据此只清「当前这条回复」的草稿，不动历史消息
      - （done 事件由路由层在循环结束后补发，携带 final_answer 做权威覆盖）

    降级约定：complete 函数如果没有 with_tools/with_stream 属性（测试里的假模型），
    直接走纯文本路径——「能力探测」式设计，测试不需要 mock Ollama。
    """
    if not hasattr(complete, "with_tools") and not hasattr(complete, "with_stream"):
        yield {"type": "token", "content": complete(answer_prompt(rewritten, history))}
        return

    from app.services.bot import profile_line
    from app.services.profile import get_profile

    # 用户画像注入首条消息：「已知顾客：姓名张三」一行，让模型跨会话认出老顾客。
    profile = get_profile(db, user_id)
    messages = [
        {"role": "user", "content": tool_choice_prompt(rewritten, history, profile_line(profile))}
    ]
    answered = False
    for _ in range(MAX_TOOL_ROUNDS):
        parts: list[str] = []
        calls: list[ToolCall] = []
        emitted = False  # 本轮是否已向调用方吐过 token——决定异常时要不要先 reset
        try:
            for chunk in _chunks(complete, messages, MODEL_TOOLS):
                if not isinstance(chunk, ModelTurn):
                    # 纯文本流式路径（with_stream）：chunk 就是字符串 token。
                    # 一旦本轮出现过 tool_calls，后续 token 不再转发（属于作废内容）。
                    text = str(chunk)
                    if text and not calls:
                        parts.append(text)
                        emitted = True
                        yield {"type": "token", "content": text}
                    continue
                # ModelTurn 路径（with_tools）：模型本轮的完整决定——要么 content 要么 tool_calls。
                if chunk.tool_calls:
                    calls = list(chunk.tool_calls)
                if chunk.content and not calls:
                    parts.append(chunk.content)
                    emitted = True
                    yield {"type": "token", "content": chunk.content}
        except Exception:
            # 模型中途挂了：已吐过半截文字就先 reset 清屏，再发统一兜底文案——
            # 保证用户永远不会看到「半截回答」，数据库也只会存兜底文案。
            if emitted:
                yield {"type": "reset"}
            yield {"type": "token", "content": ANSWER_FALLBACK}
            return
        if calls:
            # 模型决定调工具：本轮已发出的 token 全部作废（reset），
            # 把「assistant 发起调用」和「tool 返回结果」两条消息回灌上下文。
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
            # 工具结果回灌后追加约束：强制模型只依据工具数据回答，防止自由发挥编造。
            messages.append({"role": "user", "content": answer_from_tools_prompt()})
            continue
        # 无工具调用 = 本轮就是最终回答。
        answered = True
        if not parts:
            yield {"type": "token", "content": ANSWER_FALLBACK}
        return
    # 三轮都在调工具：放弃，给兜底文案（防死循环的最后一道闸）。
    if not answered:
        yield {"type": "token", "content": ANSWER_FALLBACK}


def _chunks(complete, messages: list[dict], tools: list[dict]):
    """统一两种模型调用形态：with_stream 逐 token yield；with_tools 一次性返回。"""
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
    """执行单个工具调用。

    安全核心：use_request_tools 把 db/embed/user_id/用户原话/槽位放进 contextvar，
    工具函数从 contextvar 取身份——身份是服务端的事实，不经过模型的参数空间。
    """
    with use_request_tools(db, embed, user_id=user_id, message=message, slots=slots):
        if call.name == "search_knowledge":
            # 模型没给 query 时退回改写后的完整问题。
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
                # 单工具失败不拖垮整个对话：降级为「未找到」让模型继续。
                found = "未找到"
            return str(found), lookup_result
    return "", lookup_result
