"""Phase 06 验收测试：自然语言 → 正确 Tool → 正确参数 → MySQL → 正确结果。

依赖本机 MySQL（initialized_db fixture）与 Ollama（qwen3:8b Tool Calling）。
"""

from __future__ import annotations

import pytest

from app.agents.tool_agent import run_tool_agent

pytestmark = [pytest.mark.slow, pytest.mark.usefixtures("initialized_db")]


def _called(tool_calls: list[dict], name: str) -> bool:
    return any(c["name"] == name for c in tool_calls)


def _any_keyword(text: str, keywords: list[str]) -> bool:
    """文本包含任一关键词即通过（兼容 LLM 措辞变化）。"""
    return any(kw in text for kw in keywords)


def test_tool_agent_get_order() -> None:
    r = run_tool_agent("查询订单 ORD20260001 的状态")
    assert _any_keyword(r["answer"], ["已发货", "发货", "出库"])
    assert any(
        c["name"] == "get_order" and c["args"].get("order_id") == "ORD20260001"
        for c in r["tool_calls"]
    )


def test_tool_agent_product_price_by_name() -> None:
    r = run_tool_agent("K87 机械键盘现在多少钱？")
    assert _any_keyword(r["answer"], ["299", "299.00"])
    assert _called(r["tool_calls"], "search_products")


def test_tool_agent_get_inventory() -> None:
    r = run_tool_agent("P003 的库存还有多少？")
    assert _any_keyword(r["answer"], ["45", "库存"])
    assert _called(r["tool_calls"], "get_inventory")


def test_tool_agent_get_user_orders() -> None:
    r = run_tool_agent("用户 U0002 有哪些订单？")
    assert "ORD20260003" in r["answer"] or "ORD20260005" in r["answer"]
    assert _called(r["tool_calls"], "get_user_orders")


def test_tool_agent_get_order_items() -> None:
    r = run_tool_agent("订单 ORD20260006 包含哪些商品？")
    assert _called(r["tool_calls"], "get_order_items")
    assert ("P007" in r["answer"]) or ("显示器" in r["answer"])


def test_tool_agent_order_not_found() -> None:
    r = run_tool_agent("查询订单 ORD99999999 的状态")
    assert _any_keyword(r["answer"], ["不存在", "未找到", "找不到", "没有"])
    assert _called(r["tool_calls"], "get_order")


def test_tool_agent_missing_param_asks_user() -> None:
    r = run_tool_agent("帮我查一下订单状态")
    # 缺少订单号时不应猜测参数，应询问用户
    assert not r["tool_calls"]
    assert "订单号" in r["answer"]
