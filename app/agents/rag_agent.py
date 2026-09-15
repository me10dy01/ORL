"""RAG Agent（Phase 05 / 模型职责重构）。

将 Phase 04 的 RAG 能力封装为独立 LangGraph 子图：

    START → retrieve → generate → END

可选重排（RAG_RERANK_ENABLED=true）：retrieve 阶段取 rerank_top_k 个候选，
经 Reranker 重排后保留 rerank_top_n 个送入生成（默认关闭，保持基线行为）。

输出：answer + sources（溯源）。

RAG Agent 职责（AGENTS.md 第 5 条）：
- Query 接收、Embedding、Chroma Retrieval、Top-K、Context 构建、LLM 生成、溯源返回。
- 不查询/修改 MySQL、不调用业务 Tool、不修改 Conversation Memory、不修改全局状态。
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.rag import generation, reranker, vectorstore

settings = get_settings()


class RAGState(TypedDict, total=False):
    """RAG Agent 内部状态。"""

    query: str
    retrieved_docs: list[dict]
    answer: str
    sources: list[dict]


def retrieve_node(state: RAGState) -> dict:
    """检索：Query → BGE-M3 → Chroma Top-K（可选 Reranker 重排）。"""
    if settings.rag_rerank_enabled:
        candidates = vectorstore.retrieve(state["query"], top_k=settings.rerank_top_k)
        docs = reranker.get_reranker().rerank(
            state["query"], candidates, settings.rerank_top_n
        )
    else:
        docs = vectorstore.retrieve(state["query"])
    return {"retrieved_docs": docs}


def generate_node(state: RAGState) -> dict:
    """生成：基于检索上下文用 Qwen3 生成回答，并附带溯源。"""
    contexts = state["retrieved_docs"]
    answer = generation.generate_answer(state["query"], contexts)
    return {
        "answer": answer,
        "sources": [c["metadata"] for c in contexts],
    }


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
