"""Phase 11 验收：QA 能检测错误回答 / 编造事实，正确回答可通过。"""

from __future__ import annotations

import pytest

from app.agents.qa_agent import validate_answer

pytestmark = pytest.mark.slow

CTX = "知识库回答：本商城支持签收后 7 天内无理由退货。"


def test_qa_passes_correct_answer() -> None:
    r = validate_answer("商城退货政策是什么？", "本商城支持签收后 7 天内无理由退货。", CTX, True)
    assert r.passed is True
    assert r.citation_valid is True


def test_qa_detects_wrong_fact() -> None:
    r = validate_answer("商城退货政策是什么？", "本商城支持签收后 30 天内无理由退货。", CTX, True)
    assert r.passed is False


def test_qa_detects_fabrication() -> None:
    r = validate_answer("商城退货政策是什么？", "本商城支持量子计算机退货，还送火星旅行。", CTX, True)
    assert r.passed is False
