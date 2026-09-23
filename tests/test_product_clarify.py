"""品类短查询：强制 search_products + 多结果澄清（0 次 LLM）。"""

from __future__ import annotations

import pytest

from app.agents.tool_agent import needs_product_clarify, run_tool_agent

pytestmark = pytest.mark.usefixtures("initialized_db")


def test_needs_product_clarify_keyboard() -> None:
    assert needs_product_clarify("键盘") == "键盘"
    assert needs_product_clarify("  鼠标  ") == "鼠标"


def test_needs_product_clarify_skips_non_ambiguous() -> None:
    assert needs_product_clarify("K87 键盘多少钱？") is None
    assert needs_product_clarify("键盘能退货吗") is None
    assert needs_product_clarify("帮我查一下订单状态") is None
    assert needs_product_clarify("查询订单 ORD20260001") is None
    assert needs_product_clarify("") is None


def test_product_clarify_keyboard_lists_options() -> None:
    """「键盘」应强制调 search_products，多款时引导澄清。"""
    r = run_tool_agent("键盘")
    assert any(
        c["name"] == "search_products" and c["args"].get("keyword") == "键盘"
        for c in r["tool_calls"]
    )
    assert "请问您想了解哪一款" in r["answer"]
    assert "K87" in r["answer"] or "机械键盘" in r["answer"]
    assert "K60" in r["answer"] or "薄膜键盘" in r["answer"]


def test_product_clarify_no_match() -> None:
    """品类词命中规则但库中无匹配时，仍记录 tool_calls 并说明未找到。

    「平板」在关键词表中；种子数据若无平板商品则走 0 命中文案。
    """
    r = run_tool_agent("平板")
    assert any(c["name"] == "search_products" for c in r["tool_calls"])
    # 有货则列选项/摘要；无货则「未找到」
    assert ("未找到" in r["answer"]) or ("请问您想了解哪一款" in r["answer"]) or (
        "为您找到" in r["answer"]
    )
