"""Citation 逐句对齐（Phase 15 扩展）。

把回答切句，逐句回溯到检索/工具上下文，标出"无依据的句子"，
用于 QA 的细粒度幻觉检测（相比只判断"有没有引用"更精确）。

默认不在每请求链路上运行（延迟较高），供离线评估或按需开启。
"""

from __future__ import annotations

import re
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.llm import get_structured_model

MIN_SENTENCE_LEN = 4

# 按中文/英文句末标点切句（保留标点），并按换行切
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])|\n+")
# 剥离 Markdown 前缀噪声（标题/列表/表格/引用）
_MD_PREFIX_RE = re.compile(r"^\s*(#{1,6}\s*|[-*+]\s+|\d+[.、)]\s*|>\s*|\|)")
# 表格分隔行 |---|---|
_TABLE_SEP_RE = re.compile(r"^\|?[\s:|-]+\|?$")

ALIGN_SYSTEM_PROMPT = (
    "你是溯源校验员。给定【参考上下文】与【待校验回答】，"
    "请找出回答中无法在参考上下文中找到依据的句子，输出它们的编号。\n"
    "注意：\n"
    "1. 只依据参考上下文判断，不要使用你自己的知识。\n"
    "2. 事实性陈述必须有上下文支持；纯粹的过渡语/礼貌用语不算无依据。\n"
    "3. 如果全部句子都有依据，返回空列表。"
)


class AlignmentResult(BaseModel):
    """逐句归因结果。"""

    unsupported_indices: list[int] = Field(
        default_factory=list, description="无上下文依据的句子编号（0-based）"
    )
    reason: str = Field(default="", description="总体说明")


def split_sentences(text: str) -> list[str]:
    """把回答切分为句子，并过滤 Markdown 噪声与过短碎片。"""
    out: list[str] = []
    for raw in _SENT_SPLIT_RE.split(text or ""):
        if not raw:
            continue
        piece = raw.strip()
        if not piece or _TABLE_SEP_RE.match(piece):
            continue
        piece = _MD_PREFIX_RE.sub("", piece).strip()
        if len(piece) >= MIN_SENTENCE_LEN:
            out.append(piece)
    return out


def _validate_indices(indices: list[int], n: int) -> list[int]:
    """去重、过滤越界，并按升序返回。"""
    seen: set[int] = set()
    out: list[int] = []
    for i in indices:
        if isinstance(i, int) and 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    return sorted(out)


def align_citations(answer: str, contexts: list[str]) -> dict:
    """逐句对齐：返回句子、无依据句子及其占比。

    contexts 为支撑文本列表（检索片段内容或子 Agent 回答）。
    """
    sentences = split_sentences(answer)
    if not sentences:
        return {
            "sentences": [],
            "unsupported_indices": [],
            "unsupported_sentences": [],
            "supported_ratio": 1.0,
        }

    context_text = "\n\n".join(c for c in contexts if c) or "（无上下文）"
    numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences))
    prompt = (
        f"参考上下文：\n{context_text}\n\n"
        f"待校验回答（按句编号）：\n{numbered}\n\n"
        "请输出无依据句子的编号列表。"
    )
    llm = get_structured_model().with_structured_output(AlignmentResult)
    result = cast(
        AlignmentResult,
        llm.invoke(
            [SystemMessage(content=ALIGN_SYSTEM_PROMPT), HumanMessage(content=prompt)]
        ),
    )

    unsupported = _validate_indices(result.unsupported_indices, len(sentences))
    return {
        "sentences": sentences,
        "unsupported_indices": unsupported,
        "unsupported_sentences": [sentences[i] for i in unsupported],
        "supported_ratio": 1.0 - len(unsupported) / len(sentences),
    }
