"""QA / Validator Agent（Phase 11）。

检查最终回答的可靠性（AGENTS.md 第 17 条），输出结构化结论：
    {passed, issues, citation_valid, reason}
"""

from __future__ import annotations

from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app import profiling
from app.llm import get_structured_model

MAX_RETRY = 2

# 规则质检阈值（保守：只做结构性判定，避免误伤好答案）
MIN_ANSWER_LEN = 10
MIN_CONTEXT_OVERLAP = 0.45

QA_SYSTEM_PROMPT = (
    "你是客服回答质检员（QA/Validator）。请检查以下回答是否可靠，输出结构化结论。\n"
    "检查项：\n"
    "1. 是否回答了用户问题\n"
    "2. 是否存在明显事实错误\n"
    "3. 是否与参考上下文冲突\n"
    "4. 是否出现无依据的事实（编造）\n"
    "5. 引用来源是否有效（若使用了知识库，需有来源）\n"
    "任一项不通过则 passed=false，并在 issues 中逐条说明。"
)


class QAResult(BaseModel):
    """QA 结构化结果。"""

    passed: bool = Field(description="是否通过质检")
    issues: list[str] = Field(default_factory=list, description="不通过的具体问题")
    citation_valid: bool = Field(description="引用来源是否有效")
    reason: str = Field(description="总体结论说明")


def validate_answer(
    user_query: str,
    draft_answer: str,
    context_text: str,
    has_citations: bool,
) -> QAResult:
    """对草稿回答做质检。"""
    prompt = (
        f"用户问题：{user_query}\n\n"
        f"参考上下文：\n{context_text}\n\n"
        f"待检查回答：{draft_answer}\n\n"
        f"是否有引用来源：{'有' if has_citations else '无'}\n"
    )
    llm = get_structured_model().with_structured_output(QAResult)
    with profiling.stage("qa_llm"):
        profiling.mark("llm_calls")
        return cast(
            QAResult,
            llm.invoke(
                [SystemMessage(content=QA_SYSTEM_PROMPT), HumanMessage(content=prompt)]
            ),
        )


def _verdict(
    passed: bool,
    reason: str,
    *,
    citation_valid: bool,
    issues: list[str] | None = None,
) -> QAResult:
    return QAResult(
        passed=passed,
        issues=issues if issues is not None else ([] if passed else [reason]),
        citation_valid=citation_valid,
        reason=reason,
    )


def _overlap_ratio(answer: str, context: str) -> float:
    """回答中的汉字出现在上下文里的比例（0~1），用于粗粒度“是否有依据”判断。"""
    chars = {c for c in answer if "\u4e00" <= c <= "\u9fff"}
    if not chars:
        return 1.0
    context_chars = set(context)
    return sum(1 for c in chars if c in context_chars) / len(chars)


def rule_check(
    draft_answer: str,
    *,
    expects_rag: bool,
    expects_tool: bool,
    has_citations: bool,
    tool_calls: list[dict],
    context_text: str,
) -> QAResult | None:
    """规则质检（0 次 LLM 调用）。

    返回 QAResult 表示规则已能判定；返回 None 表示无法判定，交由 LLM 复核。

    只做结构性判定，**不会因规则误伤而否掉好答案**：

    - 硬失败：回答为空/过短、应有溯源却缺失、应调工具却未调
    - 快速通过：结构完整，且回答与上下文词面一致
    - 其余（如词面重合度过低、疑似编造）→ None，交给 LLM
    """
    answer = (draft_answer or "").strip()

    if len(answer) < MIN_ANSWER_LEN:
        return _verdict(False, "回答为空或过短", citation_valid=has_citations)
    if expects_rag and not has_citations:
        return _verdict(False, "RAG 回答缺少溯源依据", citation_valid=False)
    if expects_tool and not tool_calls:
        return _verdict(False, "未调用任何业务工具", citation_valid=has_citations)

    if context_text and _overlap_ratio(answer, context_text) < MIN_CONTEXT_OVERLAP:
        return None  # 疑似无依据 → 交给 LLM 复核

    return _verdict(
        True,
        "规则校验通过（结构完整且与上下文一致）",
        citation_valid=has_citations or not expects_rag,
    )
