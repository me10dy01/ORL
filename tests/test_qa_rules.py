"""QA 规则先行测试（不调用 LLM，秒级）。"""

from __future__ import annotations

from app.agents.qa_agent import QAResult, rule_check

CTX = "知识库回答：星辰数码商城支持签收后 7 天内无理由退货，质量问题 15 天内可退换。"


def _check(
    answer: str,
    *,
    expects_rag: bool = False,
    expects_tool: bool = False,
    has_citations: bool = False,
    tool_calls: list[dict] | None = None,
    context_text: str = CTX,
) -> QAResult | None:
    return rule_check(
        answer,
        expects_rag=expects_rag,
        expects_tool=expects_tool,
        has_citations=has_citations,
        tool_calls=tool_calls or [],
        context_text=context_text,
    )


def test_rule_passes_complete_answer() -> None:
    verdict = _check(
        "星辰数码商城支持签收后 7 天内无理由退货，质量问题 15 天内可退换。",
        expects_rag=True,
        has_citations=True,
    )
    assert verdict is not None
    assert verdict.passed is True
    assert verdict.citation_valid is True


def test_rule_fails_when_rag_has_no_citations() -> None:
    verdict = _check(
        "星辰数码商城支持签收后 7 天内无理由退货。",
        expects_rag=True,
        has_citations=False,
    )
    assert verdict is not None
    assert verdict.passed is False
    assert verdict.citation_valid is False


def test_rule_fails_when_tool_not_called() -> None:
    verdict = _check("订单状态为已发货。", expects_tool=True, tool_calls=[])
    assert verdict is not None
    assert verdict.passed is False


def test_rule_fails_on_too_short_answer() -> None:
    verdict = _check("好的。", expects_rag=True, has_citations=True)
    assert verdict is not None
    assert verdict.passed is False


def test_rule_defers_to_llm_on_low_overlap() -> None:
    # 与上下文几乎无重合，疑似编造 → 返回 None，交给 LLM 复核（不直接判失败）
    verdict = _check(
        "量子计算机可包邮火星旅行，支持超光速发货。",
        expects_rag=True,
        has_citations=True,
    )
    assert verdict is None
