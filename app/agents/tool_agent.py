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
    "3. 订单号必须是用户亲口提供的真实订单号（如 ORD20260001），不能为了调用工具而虚构。\n"
    "4. 基于工具返回结果用自然语言回答；若工具返回数据不存在，请明确告知用户。\n"
    "5. 禁止编造数据库中没有的数据。"
)


class ToolAgentState(TypedDict):
    """Tool Agent 状态：query 为输入，answer/tool_calls 为输出，messages 为内部。

    所有 key 均由 run_tool_agent() / init_node() 初始化，故声明为必需 key。
    """

    query: str
    messages: Annotated[list[BaseMessage], add_messages]
    answer: str
    tool_calls: list[dict]


def _get_llm() -> Runnable[LanguageModelInput, AIMessage]:
    return get_chat_model().bind_tools(BUSINESS_TOOLS)


def init_node(state: ToolAgentState) -> dict:
    """把 query 转为首条用户消息。"""
    return {"messages": [HumanMessage(content=state["query"])]}


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


def _verify_tool_args(call: dict, original_query: str) -> str | None:
    """校验工具参数是否来自用户原始查询，防止 LLM 编造 ID。

    仅对强业务实体参数做来源校验；若参数未在查询中出现，返回错误提示。
    """
    query_lower = original_query.lower()
    args = call.get("args", {})
    for key, value in args.items():
        if key not in _ID_PARAM_NAMES or not isinstance(value, str):
            continue
        if value.lower() not in query_lower:
            param_label = {"order_id": "订单号", "user_id": "用户ID", "product_id": "商品ID"}[key]
            return f"参数校验失败：{param_label} {value} 并非来自用户当前问题，请向用户询问真实的{param_label}。"
    return None


def execute_tools_node(state: ToolAgentState) -> dict:
    """执行上一轮 LLM 决策出的全部工具调用，返回 ToolMessage。"""
    last = state["messages"][-1]
    calls = last.tool_calls if isinstance(last, AIMessage) else []
    writer = get_stream_writer()
    tool_messages: list[ToolMessage] = []
    original_query = state.get("query", "")
    with profiling.stage("tool_exec"):
        for call in calls:
            name = call["name"]
            args = call["args"]
            writer({"type": "tool", "name": name, "args": args})
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
                verification_error = _verify_tool_args(call, original_query)
                if verification_error:
                    content = json.dumps(
                        {
                            "ok": False,
                            "error": verification_error,
                            "error_type": "validation_error",
                        },
                        ensure_ascii=False,
                    )
                else:
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


def finalize_node(state: ToolAgentState) -> dict:
    """从 messages 提取最终回答与工具调用轨迹。"""
    messages = state["messages"]
    raw = messages[-1].content
    answer = raw if isinstance(raw, str) else str(raw)
    tool_calls = [
        {"name": c["name"], "args": c["args"]}
        for m in messages
        if isinstance(m, AIMessage)
        for c in (m.tool_calls or [])
    ]
    return {"answer": answer, "tool_calls": tool_calls}


def build_tool_agent():
    graph = StateGraph(ToolAgentState)
    graph.add_node("init", init_node)
    graph.add_node("agent", agent_node)
    graph.add_node("execute_tools", execute_tools_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "init")
    graph.add_edge("init", "agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"execute_tools": "execute_tools", "finalize": "finalize"},
    )
    graph.add_edge("execute_tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile(name="tool_agent")


tool_agent = build_tool_agent()


def run_tool_agent(query: str) -> dict:
    """独立运行 Tool Agent（便于测试与单 Agent 调用）。"""
    result = tool_agent.invoke(
        {"query": query, "messages": [], "answer": "", "tool_calls": []},
        config={"recursion_limit": 10},
    )
    return {"answer": result["answer"], "tool_calls": result.get("tool_calls", [])}
