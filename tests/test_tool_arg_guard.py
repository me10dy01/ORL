"""订单号必须来自用户原话，不能来自提示词示例或任务改写。"""

from __future__ import annotations

from app.agents.tool_agent import (
    _verify_tool_args,
    needs_order_identifier,
    tool_agent,
    user_supplied_text,
)


def test_example_order_id_in_task_query_is_not_user_text() -> None:
    state = {
        "query": "查询订单 ORD20260001 的物流状态",
        "user_query": "怎么查我的订单到哪了？",
        "history": [
            {
                "role": "assistant",
                "content": "请提供您的订单号（如ORD20260001）以便查询。",
            }
        ],
    }
    source = user_supplied_text(state)
    assert "ORD20260001" not in source
    assert needs_order_identifier(source)
    assert _verify_tool_args(
        {"args": {"order_id": "ORD20260001"}},
        source,
    )


def test_order_id_from_earlier_user_turn_is_allowed() -> None:
    state = {
        "query": "ORD20260001 到哪了",
        "user_query": "那单到哪了？",
        "history": [{"role": "user", "content": "我的订单号是 ORD20260001"}],
    }
    source = user_supplied_text(state)
    assert not needs_order_identifier(source)
    assert _verify_tool_args({"args": {"order_id": "ORD20260001"}}, source) is None


def test_missing_order_id_asks_without_calling_tools() -> None:
    result = tool_agent.invoke(
        {
            "query": "查询订单 ORD20260001 到哪了",
            "user_query": "怎么查我的订单到哪了？",
            "history": [],
            "messages": [],
            "answer": "",
            "tool_calls": [],
        },
        config={"recursion_limit": 6},
    )
    assert result["tool_calls"] == []
    assert "ORD20260001" not in result["answer"]
    assert "订单号" in result["answer"]
