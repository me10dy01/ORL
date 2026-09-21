"""RAG Agent（Phase 05 / 模型职责重构）。

将 Phase 04 的 RAG 能力封装为独立 LangGraph 子图：

    START → retrieve → generate → END

检索策略由 ``app.rag.pipeline.retrieve_for_answer`` 统一决定，Agent 不直接操作
向量库或重排器。

输出：answer + sources（溯源）。

RAG Agent 职责（AGENTS.md 第 5 条）：
- Query 接收、Embedding、Chroma Retrieval、Top-K、Context 构建、LLM 生成、溯源返回。
- 不查询/修改 MySQL、不调用业务 Tool、不修改 Conversation Memory、不修改全局状态。
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.rag import pipeline


class RAGState(TypedDict, total=False):
    """RAG Agent 内部状态。"""

    query: str
    retrieved_docs: list[dict]
    answer: str
    sources: list[dict]


def retrieve_node(state: RAGState) -> dict:
    """调用统一在线检索入口，避免 Agent 与 RAG 基础设施耦合。"""
    return {"retrieved_docs": pipeline.retrieve_for_answer(state["query"])}


def generate_node(state: RAGState) -> dict:
    """生成：与 pipeline.compose_answer 共用同一处，避免再写一遍检索后生成。"""
    composed = pipeline.compose_answer(state["query"], state["retrieved_docs"])
    return {"answer": composed["answer"], "sources": composed["sources"]}


def build_rag_agent():
    """构建并编译 RAG Agent 子图。"""
    graph = StateGraph(RAGState)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile(name="rag_agent")


# 模块级单例，供独立运行与后续主图（Phase 08）复用
rag_agent = build_rag_agent()


def run_rag_agent(query: str) -> dict:
    """独立运行 RAG Agent，返回 answer + sources。"""
    initial: RAGState = {
        "query": query,
        "retrieved_docs": [],
        "answer": "",
        "sources": [],
    }
    result = rag_agent.invoke(initial)
    return {"answer": result["answer"], "sources": result["sources"]}
