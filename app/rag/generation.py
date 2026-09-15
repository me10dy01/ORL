"""LLM 回答生成（Qwen3）。

严格基于检索结果；知识库无足够依据时明确说明，不强行生成事实。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama

from app import profiling
from app.llm import get_chat_model


def get_llm() -> ChatOllama:
    return get_chat_model()


def generate_answer(query: str, contexts: list[dict]) -> str:
    """基于检索上下文生成回答。contexts 为空时明确说明无依据。"""
    if not contexts:
        return "知识库中没有找到足够依据，无法回答该问题。"

    context_text = "\n\n".join(
        f"[资料 {i}] {c['content']}" for i, c in enumerate(contexts, start=1)
    )
    prompt = (
        "你是星辰数码商城的客服助手，请严格基于以下知识库内容回答用户问题。\n"
        "要求：\n"
        "1. 简明扼要，直接给结论，不超过 300 字，不要罗列知识库中的全部条款。\n"
        "2. 如果知识库内容不足以回答，请明确说明知识库中没有找到足够依据，不要编造事实。\n\n"
        f"知识库内容：\n{context_text}\n\n"
        f"用户问题：{query}\n\n"
        "回答："
    )
    llm = get_llm()
    with profiling.stage("rag_gen"):
        profiling.mark("llm_calls")
        result = llm.invoke([HumanMessage(content=prompt)])
        content = result.content
        return content if isinstance(content, str) else str(content)
