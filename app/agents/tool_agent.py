"""Tool Agent（Phase 06 / Phase 08）。

让 LLM 通过 Tool Calling 使用业务数据：
    自然语言问题 → 正确 Tool → 正确参数 → MySQL → 正确结果

链路：Agent → Tool → queries → Repository → MySQL（禁止 Agent → SQL → MySQL）。

子图契约（Phase 08）：
    输入 query → 输出 answer + tool_calls
内部使用 LangGraph ReAct 循环（messages），对外不暴露。
"""

from __future__ import annotations

import json
import re
from typing import Annotated, TypedDict

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.agents.business_tools import BUSINESS_TOOLS, TOOL_BY_NAME
from app import profiling
from app.llm import get_chat_model

SYSTEM_PROMPT = (
    "你是星辰数码商城的业务查询助手，请使用提供的工具查询业务数据来回答用户问题。\n"
    "规则：\n"
    "1. 根据用户问题选择合适的工具，并生成正确的工具参数。\n"
    "2. 如果用户没有提供足够参数（如缺少订单号、商品ID、用户ID），必须向用户询问，禁止猜测、编造或假设任何ID。\n"
    "3. 订单号、商品ID、用户ID 必须出现在用户原话中。不要使用提示词、示例或你自己刚才回复里的编号。\n"
    "4. 基于工具返回结果用自然语言回答；若工具返回数据不存在，请明确告知用户。\n"
    "5. 禁止编造数据库中没有的数据。"
)

# 追问文案不能出现真实订单号，否则后续轮次会把它当成用户提供的编号。
_ASK = {
    "order_id": (
        "请提供订单号后再查询。订单号以 ORD 开头，后面是 8 位数字。"
        "您也可以在订单中心查看订单详情。"
    ),
    "user_id": "请提供用户ID后再查询。用户ID以 U 开头。",
    "product_id": "请提供商品ID后再查询。商品ID以 P 开头，后面是 3 位数字。",
}

_ORDER_ID_RE = re.compile(r"ORD\d{8}", re.IGNORECASE)
_USER_ID_RE = re.compile(r"\bU\d{3,4}\b", re.IGNORECASE)
_ORDER_LOOKUP_HINTS = (
    "我的订单",
    "订单状态",
    "订单号",
    "到哪了",
    "到了没有",
    "到了没",
    "发货了吗",
    "物流单",
    "运单",
)


class ToolAgentState(TypedDict):
    """Tool Agent 状态：query 为输入，answer/tool_calls 为输出，messages 为内部。

    所有 key 均由 run_tool_agent() / init_node() 初始化，故声明为必需 key。
    """

    query: str
    user_query: str
    history: list[dict]
    messages: Annotated[list[BaseMessage], add_messages]
    answer: str
    tool_calls: list[dict]


def _get_llm() -> Runnable[LanguageModelInput, AIMessage]:
    return get_chat_model().bind_tools(BUSINESS_TOOLS)


def user_supplied_text(state: dict) -> str:
    """只保留用户自己写过的话，不含客服回复、任务改写或提示词示例。"""
    user_query = (state.get("user_query") or "").strip()
    history = state.get("history") or []
    if user_query or history:
        parts = [user_query]
        for item in history:
            if isinstance(item, dict) and item.get("role") == "user":
                parts.append(item.get("content") or "")
        return "\n".join(parts)
    return state.get("query") or ""


def needs_order_identifier(text: str) -> bool:
    """在查订单，但用户原话里没有订单号或用户ID。"""
    if _ORDER_ID_RE.search(text) or _USER_ID_RE.search(text):
        return False
    return any(hint in text for hint in _ORDER_LOOKUP_HINTS)


def init_node(state: ToolAgentState) -> dict:
    """把 query 转为首条用户消息。缺订单号时直接追问，不调用模型。"""
    source = user_supplied_text(state)
    if needs_order_identifier(source):
        return {"messages": [AIMessage(content=_ASK["order_id"])]}
    return {"messages": [HumanMessage(content=state["query"])]}


def after_init(state: ToolAgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and not last.tool_calls:
        return "finalize"
    return "agent"


def agent_node(state: ToolAgentState) -> dict:
    llm = _get_llm()
    with profiling.stage("tool_llm"):
        profiling.mark("llm_calls")
        try:
            response = llm.invoke(
                [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
            )
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Tool Agent LLM 调用失败")
            response = AIMessage(content="抱歉，业务数据查询暂时不可用，请稍后再试。")
    return {"messages": [response]}


_ID_PARAM_NAMES = {"order_id", "user_id", "product_id"}


def _invalid_id_param(call: dict, source_text: str) -> str | None:
    """返回第一个并非来自用户原话的 ID 参数名。"""
    source_lower = source_text.lower()
    args = call.get("args") or {}
    for key, value in args.items():
        if key not in _ID_PARAM_NAMES or not isinstance(value, str):
            continue
        if not value.strip() or value.lower() not in source_lower:
            return key
    return None


def _verify_tool_args(call: dict, source_text: str) -> str | None:
    """校验工具参数是否来自用户原话，防止 LLM 编造或抄示例编号。

    仅对强业务实体参数做来源校验；若参数未在用户原话中出现，返回错误提示。
    错误文案不回显被拒绝的编号，避免下一轮再把它当成真实订单号。
    """
    key = _invalid_id_param(call, source_text)
    if key is None:
        return None
    param_label = {"order_id": "订单号", "user_id": "用户ID", "product_id": "商品ID"}[key]
    return (
        f"参数校验失败：{param_label}并非来自用户原话。"
        f"请向用户询问真实的{param_label}，不要猜测或使用示例编号。"
    )


def execute_tools_node(state: ToolAgentState) -> dict:
    """执行上一轮 LLM 决策出的全部工具调用，返回 ToolMessage。"""
    last = state["messages"][-1]
    calls = last.tool_calls if isinstance(last, AIMessage) else []
    writer = get_stream_writer()
    tool_messages: list[ToolMessage] = []
    source_text = user_supplied_text(state)
    with profiling.stage("tool_exec"):
        for call in calls:
            name = call["name"]
            args = call["args"]
            tool_fn = TOOL_BY_NAME.get(name)
            if tool_fn is None:
                content = json.dumps(
                    {
                        "ok": False,
                        "error": f"未知工具: {name}",
                        "error_type": "unknown_tool",
                    },
                    ensure_ascii=False,
                )
            else:
                verification_error = _verify_tool_args(call, source_text)
                if verification_error:
                    content = json.dumps(
                        {
                            "ok": False,
                            "error": verification_error,
                            "error_type": "validation_error",
                            "param": _invalid_id_param(call, source_text) or "order_id",
                        },
                        ensure_ascii=False,
                    )
                else:
                    writer({"type": "tool", "name": name, "args": args})
                    try:
                        content = tool_fn.invoke(args)
                    except Exception as exc:  # 工具异常需兜底返回给 LLM，不能静默吞掉
                        content = json.dumps(
                            {
                                "ok": False,
                                "error": f"工具执行异常: {exc}",
                                "error_type": "tool_error",
                            },
                            ensure_ascii=False,
                        )
            tool_messages.append(
                ToolMessage(content=content, tool_call_id=call["id"], name=name)
            )
    return {"messages": tool_messages}


def should_continue(state: ToolAgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "execute_tools"
    return "finalize"


def _tool_payload(message: ToolMessage) -> dict:
    raw = message.content
    if not isinstance(raw, str):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _rejected_call_ids(messages: list[BaseMessage]) -> set[str]:
    rejected: set[str] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if _tool_payload(message).get("error_type") == "validation_error":
            rejected.add(message.tool_call_id)
    return rejected


def after_tools(state: ToolAgentState) -> str:
    """编号校验全部失败时直接追问，不再把示例编号交回模型。"""
    messages = state["messages"]
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    if tool_messages and all(
        _tool_payload(m).get("error_type") == "validation_error" for m in tool_messages
    ):
        return "clarify"
    return "agent"


def clarify_node(state: ToolAgentState) -> dict:
    param = "order_id"
    for message in state["messages"]:
        if not isinstance(message, ToolMessage):
            continue
        payload = _tool_payload(message)
        if payload.get("error_type") == "validation_error" and payload.get("param") in _ASK:
            param = payload["param"]
            break
    return {"messages": [AIMessage(content=_ASK[param])]}


def finalize_node(state: ToolAgentState) -> dict:
    """从 messages 提取最终回答与真正执行过的工具调用。"""
    messages = state["messages"]
    raw = messages[-1].content
    answer = raw if isinstance(raw, str) else str(raw)
    rejected = _rejected_call_ids(messages)
    tool_calls = [
        {"name": c["name"], "args": c["args"]}
        for m in messages
        if isinstance(m, AIMessage)
        for c in (m.tool_calls or [])
        if c.get("id") not in rejected
    ]
    return {"answer": answer, "tool_calls": tool_calls}


def build_tool_agent():
    graph = StateGraph(ToolAgentState)
    graph.add_node("init", init_node)
    graph.add_node("agent", agent_node)
    graph.add_node("execute_tools", execute_tools_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "init")
    graph.add_conditional_edges(
        "init",
        after_init,
        {"agent": "agent", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"execute_tools": "execute_tools", "finalize": "finalize"},
    )
    graph.add_conditional_edges(
        "execute_tools",
        after_tools,
        {"agent": "agent", "clarify": "clarify"},
    )
    graph.add_edge("clarify", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(name="tool_agent")


tool_agent = build_tool_agent()


def run_tool_agent(
    query: str,
    user_query: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    """独立运行 Tool Agent（便于测试与单 Agent 调用）。

    user_query / history 缺省时把 query 视为用户原话。
    主图调用时应传入用户原话，避免任务改写把示例订单号带进来。
    """
    result = tool_agent.invoke(
        {
            "query": query,
            "user_query": query if user_query is None else user_query,
            "history": history or [],
            "messages": [],
            "answer": "",
            "tool_calls": [],
        },
        config={"recursion_limit": 10},
    )
    return {"answer": result["answer"], "tool_calls": result.get("tool_calls", [])}
