"""LLM-as-Reranker。

本项目只使用主 LLM 对向量召回的候选片段重新排序。中文商城知识库的
离线评测表明，该实现优于曾使用的 ONNX cross-encoder；删除后者也避免了
额外模型下载、配置漂移和运行时降级路径的不透明性。
"""

from __future__ import annotations

import logging
from typing import Protocol, cast, runtime_checkable

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app import profiling
from app.llm import get_structured_model

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "你是检索重排助手，只按相关度排序候选片段，不生成其他内容。"
_MAX_DOC_CHARS = 800


class RerankResult(BaseModel):
    """LLM 输出的候选索引排序。"""

    indices: list[int] = Field(description="候选片段按相关度从高到低排序的编号列表")


@runtime_checkable
class Reranker(Protocol):
    """对候选文档按与查询的相关度重新排序。"""

    def rerank(self, query: str, documents: list[dict], top_n: int) -> list[dict]:
        """返回按相关度降序排列的前 top_n 个候选。"""
        ...


class LLMReranker:
    """使用 Qwen3 一次性输出候选编号排序。"""

    def rerank(self, query: str, documents: list[dict], top_n: int) -> list[dict]:
        if not documents or top_n <= 0:
            return []
        if len(documents) <= top_n:
            return documents[:top_n]

        numbered = "\n\n".join(
            f"[{i}] {candidate.get('content', '')[:_MAX_DOC_CHARS]}"
            for i, candidate in enumerate(documents)
        )
        prompt = (
            f"查询：{query}\n\n候选片段：\n{numbered}\n\n"
            "请按与查询的相关度从高到低，输出候选片段的编号列表。"
        )
        try:
            llm = get_structured_model().with_structured_output(RerankResult)
            with profiling.stage("reranker"):
                profiling.mark("llm_calls")
                result = cast(
                    RerankResult,
                    llm.invoke(
                        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=prompt)]
                    ),
                )
        except Exception:
            # 不切换到其他模型；LLM 不可用时保留向量召回顺序，RAG 仍可回答。
            logger.exception("LLM 重排失败，保留向量召回顺序")
            return documents[:top_n]
        order = _validate_order(result.indices, len(documents))
        return [documents[i] for i in order[:top_n]]


_reranker = LLMReranker()


def get_reranker(impl: str | None = None) -> Reranker:
    """返回唯一支持的 LLM 重排器。

    ``impl`` 仅为兼容旧调用签名保留；非 ``llm`` 值会被忽略并记录告警，
    确保线上不会悄然切换回已移除的模型。
    """
    if impl not in (None, "llm"):
        logger.warning("忽略已移除的 reranker 配置: %s；统一使用 llm", impl)
    return _reranker


def rerank(query: str, candidates: list[dict], top_n: int) -> list[dict]:
    """兼容入口：使用 LLM 重排候选片段。"""
    return _reranker.rerank(query, candidates, top_n)


def _validate_order(indices: list[int], n: int) -> list[int]:
    """过滤非法/重复编号，并按原始顺序补齐遗漏候选。"""
    seen: set[int] = set()
    order: list[int] = []
    for index in indices:
        if 0 <= index < n and index not in seen:
            order.append(index)
            seen.add(index)
    order.extend(index for index in range(n) if index not in seen)
    return order


__all__ = ["Reranker", "LLMReranker", "get_reranker", "rerank"]
