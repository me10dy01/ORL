"""Main Graph（Phase 07-11）。

Multi-Agent Workflow：
    START → Rewrite → Supervisor → Route → RAG / Tool / Mixed → Synthesis
          → QA → (Pass / Retry) → Save Memory → END

Phase 08：RAG 与 Tool 以原生 LangGraph 子图嵌入。
Phase 09：Query Rewrite + Conversation Memory。
Phase 11：QA/Validator + 有限 Retry（MAX_RETRY=2）。
"""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app import cache, memory, profiling
from app.agents.citation_align import align_citations
from app.agents.qa_agent import MAX_RETRY, rule_check, validate_answer
from app.agents.query_rewrite import rewrite_query
from app.agents.rag_agent import rag_agent, run_rag_agent
from app.agents.supervisor import route_query
from app.agents.tool_agent import (
    needs_order_identifier,
    run_tool_agent,
    tool_agent,
    user_supplied_text,
)
from app.config import get_settings
from app.llm import get_chat_model

settings = get_settings()
logger = logging.getLogger(__name__)


class MainState(TypedDict):
    """主图状态（Workflow State，AGENTS.md 第 13 条）。

    所有 key 均由 _initial_state() 在调用时初始化为完整值，
    因此声明为必需 key（避免 total=False 导致的可选性歧义）。
    """

    user_query: str
    conversation_id: str
    history: list[dict]
    rewritten_query: str
    route: str
    reason: str
    tasks: list[dict]
    route_decision: str

    # 子图共享契约 key
    query: str
    answer: str
    sources: list[dict]
    tool_calls: list[dict]

    # 汇总结果
    rag_result: dict
    tool_result: dict
    citations: list[dict]

    # 缓存
    cache_hit: bool
    cache_payload: dict

    # 合成与质检
    draft_answer: str
    qa_result: dict
    retry_count: int
    align_citations: bool
    final_answer: str

    # 降级标记：重试耗尽后返回安全回答而非未过检草稿
    degraded: bool

    # 性能剖析（各阶段耗时 ms 与 LLM 调用次数）
    profile: dict


# ---- 节点 ----


def rewrite_node(state: MainState) -> dict:
    history = memory.get_recent_messages(state["conversation_id"], limit=10)
    rewritten = (
        rewrite_query(history, state["user_query"]) if history else state["user_query"]
    )
    return {"history": history, "rewritten_query": rewritten}


def cache_gate_node(state: MainState) -> dict:
    """改写后先查缓存：命中则跳过整条 LLM 链路。"""
    if not settings.answer_cache_enabled:
        return {"cache_hit": False, "cache_payload": {}}
    with profiling.stage("cache_lookup"):
        payload = cache.get(state["rewritten_query"] or state["user_query"])
    # 只复用知识库回答。订单/库存会变，tool 与 mixed 的旧缓存视为未命中。
    if payload is not None and payload.get("route") != "rag":
        payload = None
    return {"cache_hit": payload is not None, "cache_payload": payload or {}}


def _cache_decision(state: MainState) -> str:
    return "hit" if state.get("cache_hit") else "miss"


def cache_finalize_node(state: MainState) -> dict:
    """命中缓存：直接恢复路由、溯源与最终答案（0 次 LLM 调用）。"""
    payload = state.get("cache_payload") or {}
    answer = payload.get("final_answer", "")
    return {
        "route": payload.get("route", "rag"),
        "reason": "命中缓存",
        "tasks": payload.get("tasks", []),
        "draft_answer": answer,
        "final_answer": answer,
        "citations": payload.get("citations", []),
        "qa_result": payload.get("qa_result", {}),
    }


def supervisor_node(state: MainState) -> dict:
    query = state.get("rewritten_query") or state["user_query"]
    result = route_query(query)
    return {
        "route": result.route,
        "reason": result.reason,
        "tasks": [t.model_dump() for t in result.tasks],
        "route_decision": result.decision_path,
    }


def _task_query(tasks: list[dict], agent: str) -> str:
    for t in tasks:
        if t.get("agent") == agent:
            return t.get("query") or ""
    return ""


def prepare_rag_node(state: MainState) -> dict:
    return {"query": _task_query(state["tasks"], "rag")}


def prepare_tool_node(state: MainState) -> dict:
    return {"query": _task_query(state["tasks"], "tool")}


def collect_rag_node(state: MainState) -> dict:
    return {
        "rag_result": {"answer": state["answer"], "sources": state["sources"]},
        "citations": state["sources"],
    }


def collect_tool_node(state: MainState) -> dict:
    return {
        "tool_result": {
            "answer": state["answer"],
            "tool_calls": state.get("tool_calls", []),
        }
    }


def _route_decision(state: MainState) -> str:
    return state["route"]


def mixed_parallel_node(state: MainState) -> dict:
    """mixed：RAG 与 Tool 互不依赖；默认串行，可配置为并行。

    两个子图都读取 state key "query"，若用图级 fan-out 会产生键冲突，
    因此在节点内用线程池并发调用子图（各自独立输入，互不干扰）。

    实测（RTX 4060 Laptop 8GB，单卡）：并行无收益（180.7/205.8s vs
    串行 148.7/199.3s），因为 Ollama 在单卡上串行执行推理；
    故 settings.mixed_parallel 默认为 False，多卡/远程 LLM 时可开启。
    """
    rag_query = _task_query(state["tasks"], "rag")
    tool_query = _task_query(state["tasks"], "tool")

    rag_result: dict | None = None
    tool_result: dict | None = None

    if settings.mixed_parallel:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures: dict[str, Future] = {}
            # 每个子线程拷贝独立 context，保证 Profiler 埋点跨线程生效
            if rag_query:
                futures["rag"] = pool.submit(
                    contextvars.copy_context().run, run_rag_agent, rag_query
                )
            if tool_query:
                futures["tool"] = pool.submit(
                    contextvars.copy_context().run,
                    run_tool_agent,
                    tool_query,
                    state["user_query"],
                    state.get("history") or [],
                )
            if "rag" in futures:
                rag_result = futures["rag"].result()
            if "tool" in futures:
                tool_result = futures["tool"].result()
    else:
        if rag_query:
            rag_result = run_rag_agent(rag_query)
        if tool_query:
            tool_result = run_tool_agent(
                tool_query,
                user_query=state["user_query"],
                history=state.get("history") or [],
            )

    updates: dict = {}
    if rag_result:
        updates["rag_result"] = {
            "answer": rag_result["answer"],
            "sources": rag_result["sources"],
        }
        updates["citations"] = rag_result["sources"]
    if tool_result:
        updates["tool_result"] = tool_result
        # 必须同时回写 tool_calls：QA 规则会据此判断“是否真的调用了业务工具”
        updates["tool_calls"] = tool_result.get("tool_calls", [])
    return updates


def _synthesize(
    user_query: str,
    rag_answer: str | None,
    tool_answer: str | None,
    feedback: list[str],
) -> str:
    """LLM 合成最终回答，重试时带上质检反馈。"""
    parts: list[str] = []
    if rag_answer:
        parts.append(f"【知识库】{rag_answer}")
    if tool_answer:
        parts.append(f"【业务数据】{tool_answer}")
    if not parts:
        return "未能生成回答。"

    source_text = "\n\n".join(parts)
    feedback_text = ""
    if feedback:
        feedback_text = (
            "\n\n上一轮质检未通过，请修正以下问题后重新生成：\n"
            + "\n".join(f"- {i}" for i in feedback)
        )
    prompt = (
        "你是星辰数码商城的客服。请基于以下各 Agent 的结果，合成一个完整、准确、自然的最终回答，"
        "不要添加各结果之外的事实。\n"
        "要求：简明扼要，直接给结论，不超过 300 字，不要罗列全部条款。\n\n"
        f"用户问题：{user_query}\n\n"
        f"各 Agent 结果：\n{source_text}\n"
        f"{feedback_text}\n\n"
        "最终回答："
    )
    llm = get_chat_model()
    with profiling.stage("synthesis"):
        profiling.mark("llm_calls")
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content
        return content if isinstance(content, str) else str(content)


def synthesis_node(state: MainState) -> dict:
    rag = state.get("rag_result") or {}
    tool = state.get("tool_result") or {}
    feedback = (state.get("qa_result") or {}).get("issues") or []

    # 单意图 RAG 无反馈时，直接使用 RAG 答案，跳过 synthesis LLM 调用（省 ~6s）
    route = state.get("route", "")
    rag_answer = rag.get("answer")
    if route == "rag" and rag_answer and not feedback:
        return {"draft_answer": rag_answer}

    # 只是在追问订单号时，不要再合成，避免模型把示例订单号写进回答。
    tool_answer = tool.get("answer")
    if (
        route == "tool"
        and tool_answer
        and not (state.get("tool_calls") or [])
        and not feedback
        and needs_order_identifier(user_supplied_text(state))
    ):
        return {"draft_answer": tool_answer}

    try:
        draft = _synthesize(
            state.get("rewritten_query") or state["user_query"],
            rag_answer,
            tool.get("answer"),
            feedback,
        )
    except Exception:
        logger.exception("synthesis_node 调用 LLM 失败")
        draft = "抱歉，当前服务暂时不可用，请稍后再试。"
    return {"draft_answer": draft}


def qa_node(state: MainState) -> dict:
    rag = state.get("rag_result") or {}
    tool = state.get("tool_result") or {}
    context_parts: list[str] = []
    if rag.get("answer"):
        context_parts.append(f"知识库回答：{rag['answer']}")
    if tool.get("answer"):
        context_parts.append(f"业务数据：{tool['answer']}")
    context_text = "\n\n".join(context_parts) or "（无上下文）"

    has_citations = bool(state.get("citations"))
    route = state.get("route", "")
    expects_rag = route in ("rag", "mixed")
    expects_tool = route in ("tool", "mixed")

    mode = settings.qa_mode
    qa_dict: dict | None = None

    with profiling.stage("qa"):
        # 1) 规则先行：能判定就不调 LLM（实测 LLM 质检约 33s/次）
        if mode in ("rule_first", "rule_only"):
            verdict = rule_check(
                state["draft_answer"],
                expects_rag=expects_rag,
                expects_tool=expects_tool,
                has_citations=has_citations,
                tool_calls=state.get("tool_calls") or [],
                context_text=context_text,
                missing_identifier=needs_order_identifier(user_supplied_text(state)),
            )
            if verdict is not None:
                qa_dict = verdict.model_dump()
                qa_dict["checked_by"] = "rule"

        # 2) 规则无法判定：交给 LLM 复核（rule_only 模式下保守通过）
        if qa_dict is None:
            if mode == "rule_only":
                qa_dict = {
                    "passed": True,
                    "issues": [],
                    "citation_valid": has_citations or not expects_rag,
                    "reason": "仅规则校验（词面重合度低，未复核）",
                    "checked_by": "rule_only",
                }
            else:
                try:
                    llm_result = validate_answer(
                        state.get("rewritten_query") or state["user_query"],
                        state["draft_answer"],
                        context_text,
                        has_citations,
                    )
                    qa_dict = llm_result.model_dump()
                    qa_dict["checked_by"] = "llm"
                except Exception:
                    logger.exception("qa_node LLM 质检失败，保守通过")
                    qa_dict = {
                        "passed": True,
                        "issues": [],
                        "citation_valid": has_citations or not expects_rag,
                        "reason": "QA 检测不可用，保守通过",
                        "checked_by": "fallback",
                    }

        # 3) 可选的逐句归因（默认关闭）：标出无上下文依据的句子
        if state.get("align_citations"):
            alignment = align_citations(state["draft_answer"], context_parts)
            qa_dict["unsupported_sentences"] = alignment["unsupported_sentences"]
            qa_dict["supported_ratio"] = alignment["supported_ratio"]
            if alignment["unsupported_sentences"]:
                qa_dict["passed"] = False
                qa_dict["issues"] = list(qa_dict.get("issues") or []) + [
                    f"无依据句子：{s}" for s in alignment["unsupported_sentences"]
                ]

    return {"qa_result": qa_dict}


def _qa_decision(state: MainState) -> str:
    qa = state.get("qa_result") or {}
    if qa.get("passed"):
        return "finalize"
    if state.get("retry_count", 0) < MAX_RETRY:
        return "retry"
    # 重试耗尽仍不过检 → 安全降级，而非返回未过检草稿
    return "degrade"


def retry_node(state: MainState) -> dict:
    return {"retry_count": state.get("retry_count", 0) + 1}


def _retry_decision(state: MainState) -> str:
    """重试策略：第 1 次带质检反馈重新合成；第 2 次回 Supervisor 重新规划。"""
    if state.get("retry_count", 0) <= 1:
        return "resynthesize"
    return "replan"


def replan_node(state: MainState) -> dict:
    """QA 二次失败 → 回 Supervisor 重规划（携带质检反馈，清空旧证据）。

    Supervisor 仍是架构角色：重新生成结构化路由与任务，
    由对应 Agent 重新取证（不直接访问 MySQL/Chroma）。
    """
    query = state.get("rewritten_query") or state["user_query"]
    feedback = (state.get("qa_result") or {}).get("issues") or []
    result = route_query(query, qa_feedback=feedback or None)
    return {
        "route": result.route,
        "reason": f"QA 重规划：{result.reason}",
        "tasks": [t.model_dump() for t in result.tasks],
        "route_decision": result.decision_path,
        # 清空上一轮证据，避免旧子图结果泄漏进新路由的合成
        "rag_result": {},
        "tool_result": {},
        "citations": [],
        "tool_calls": [],
    }


DEGRADED_ANSWER = (
    "抱歉，这个问题我暂时无法给出足够可靠的回答。"
    "为避免误导您，建议您换个方式描述问题，或联系人工客服获取进一步帮助。"
)


def degrade_node(state: MainState) -> dict:
    """重试耗尽仍不过检：返回安全降级回答，绝不把未过检草稿当最终答案。"""
    return {"final_answer": DEGRADED_ANSWER, "degraded": True}


def finalize_node(state: MainState) -> dict:
    return {"final_answer": state["draft_answer"]}


def cache_store_node(state: MainState) -> dict:
    """把本轮结果写入缓存，供相同问题复用。

    守卫：仅 QA 通过的回答才写缓存——未过检/降级回答不允许污染缓存。
    """
    if not settings.answer_cache_enabled:
        return {}
    if state.get("route") != "rag":
        return {}
    if not (state.get("qa_result") or {}).get("passed"):
        return {}
    answer = state.get("final_answer") or ""
    if not answer:
        return {}
    cache.put(
        state.get("rewritten_query") or state["user_query"],
        {
            "route": state.get("route", ""),
            "reason": state.get("reason", ""),
            "tasks": state.get("tasks", []),
            "final_answer": answer,
            "citations": state.get("citations", []),
            "qa_result": state.get("qa_result", {}),
        },
    )
    return {}


def save_memory_node(state: MainState) -> dict:
    memory.add_message(state["conversation_id"], "user", state["user_query"])
    memory.add_message(state["conversation_id"], "assistant", state["final_answer"])
    return {}


# ---- 构建图 ----


def build_main_graph():
    graph = StateGraph(MainState)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("cache_gate", cache_gate_node)
    graph.add_node("cache_finalize", cache_finalize_node)
    graph.add_node("cache_store", cache_store_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("prepare_rag", prepare_rag_node)
    graph.add_node("rag", rag_agent)
    graph.add_node("collect_rag", collect_rag_node)
    graph.add_node("prepare_tool", prepare_tool_node)
    graph.add_node("tool", tool_agent)
    graph.add_node("collect_tool", collect_tool_node)
    graph.add_node("mixed", mixed_parallel_node)
    graph.add_node("synthesis", synthesis_node)
    graph.add_node("qa", qa_node)
    graph.add_node("retry", retry_node)
    graph.add_node("replan", replan_node)
    graph.add_node("degrade", degrade_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("save_memory", save_memory_node)

    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "cache_gate")
    graph.add_conditional_edges(
        "cache_gate",
        _cache_decision,
        {"hit": "cache_finalize", "miss": "supervisor"},
    )
    graph.add_conditional_edges(
        "supervisor",
        _route_decision,
        {"rag": "prepare_rag", "tool": "prepare_tool", "mixed": "mixed"},
    )
    graph.add_edge("prepare_rag", "rag")
    graph.add_edge("rag", "collect_rag")
    graph.add_edge("collect_rag", "synthesis")
    graph.add_edge("prepare_tool", "tool")
    graph.add_edge("tool", "collect_tool")
    graph.add_edge("collect_tool", "synthesis")
    graph.add_edge("mixed", "synthesis")

    graph.add_edge("synthesis", "qa")
    graph.add_conditional_edges(
        "qa",
        _qa_decision,
        {"finalize": "finalize", "retry": "retry", "degrade": "degrade"},
    )
    graph.add_conditional_edges(
        "retry",
        _retry_decision,
        {"resynthesize": "synthesis", "replan": "replan"},
    )
    graph.add_conditional_edges(
        "replan",
        _route_decision,
        {"rag": "prepare_rag", "tool": "prepare_tool", "mixed": "mixed"},
    )
    graph.add_edge("degrade", "cache_store")
    graph.add_edge("finalize", "cache_store")
    graph.add_edge("cache_store", "save_memory")
    graph.add_edge("cache_finalize", "save_memory")
    graph.add_edge("save_memory", END)
    return graph.compile(name="main_graph")


main_graph = build_main_graph()


def _initial_state(
    user_query: str, conversation_id: str, align_citations: bool = False
) -> MainState:
    """构造主图完整初始状态。"""
    return {
        "user_query": user_query,
        "conversation_id": conversation_id,
        "history": [],
        "rewritten_query": "",
        "route": "",
        "reason": "",
        "tasks": [],
        "route_decision": "",
        "query": "",
        "answer": "",
        "sources": [],
        "tool_calls": [],
        "rag_result": {},
        "tool_result": {},
        "citations": [],
        "draft_answer": "",
        "qa_result": {},
        "retry_count": 0,
        "align_citations": align_citations,
        "cache_hit": False,
        "cache_payload": {},
        "final_answer": "",
        "degraded": False,
        "profile": {},
    }


_NODE_STATUS = {
    "rewrite": "正在理解上下文",
    "supervisor": "正在分析问题",
    "replan": "正在重新规划任务",
    "rag": "正在检索知识库",
    "tool": "正在查询业务数据",
    "synthesis": "正在生成回答",
    "qa": "正在验证回答",
}


def _result_from_state(state: dict, profile: dict) -> dict:
    """从主图最终状态组装调用方需要的结果。状态合并只发生在 iter_chat_events。"""
    return {
        "route": state.get("route", ""),
        "reason": state.get("reason", ""),
        "tasks": state.get("tasks", []),
        "route_decision": state.get("route_decision", ""),
        "rewritten_query": state.get("rewritten_query", ""),
        "final_answer": state.get("final_answer", ""),
        "citations": state.get("citations", []),
        "qa_passed": (state.get("qa_result") or {}).get("passed", False),
        "retry_count": state.get("retry_count", 0),
        "degraded": state.get("degraded", False),
        "tool_calls": (state.get("tool_result") or {}).get("tool_calls", []),
        "qa_result": state.get("qa_result", {}),
        "cache_hit": state.get("cache_hit", False),
        "profile": profile,
    }


def iter_chat_events(
    user_query: str,
    conversation_id: str = "default",
    sentence_alignment: bool | None = None,
) -> Iterator[dict]:
    """唯一的主图执行入口。产出用户可见事件，done 上附带完整 result。

    同步调用方读取 done["result"]。流式调用方丢掉 result，只转发可见字段。
    """
    align = (
        settings.qa_sentence_alignment
        if sentence_alignment is None
        else sentence_alignment
    )
    initial = _initial_state(user_query, conversation_id, align_citations=align)
    state: dict = dict(initial)

    with profiling.profiler_scope() as prof:
        for namespace, mode, chunk in main_graph.stream(
            initial,
            stream_mode=["updates", "messages", "custom"],
            subgraphs=True,
        ):
            if namespace != ():
                if mode == "custom":
                    yield chunk
                continue

            if mode == "updates":
                for node, update in chunk.items():
                    if update:
                        state.update(update)
                    msg = _NODE_STATUS.get(node)
                    if msg:
                        yield {"type": "status", "message": msg}
            elif mode == "messages":
                msg, meta = chunk
                if meta.get("langgraph_node") == "synthesis":
                    content = getattr(msg, "content", "") or ""
                    if content:
                        yield {"type": "token", "content": content}
            elif mode == "custom":
                yield chunk

        result = _result_from_state(state, prof.snapshot())
        yield {
            "type": "done",
            "final_answer": result["final_answer"],
            "citations": result["citations"],
            "qa_passed": result["qa_passed"],
            "retry_count": result["retry_count"],
            "degraded": result["degraded"],
            "profile": result["profile"],
            "result": result,
        }


def run_main_graph(
    user_query: str,
    conversation_id: str = "default",
    sentence_alignment: bool | None = None,
) -> dict:
    """把同一次事件流收完，返回 done 里的结果。不再单独 invoke 主图。"""
    result: dict | None = None
    for event in iter_chat_events(
        user_query, conversation_id, sentence_alignment=sentence_alignment
    ):
        if event.get("type") == "done":
            payload = event.get("result")
            if isinstance(payload, dict):
                result = payload
    if result is None:
        raise RuntimeError("主图未返回结果")
    return result
