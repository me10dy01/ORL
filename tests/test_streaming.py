"""Phase 12 验收测试：流式输出执行状态、工具状态与最终回答。"""

from __future__ import annotations

import uuid

import pytest

from app.streaming import stream_chat

pytestmark = pytest.mark.slow


def test_stream_emits_status_tool_and_answer(fresh_cache: None) -> None:
    cid = f"stream-{uuid.uuid4().hex[:8]}"
    events = list(stream_chat("商城退货政策是什么？", conversation_id=cid))

    types = {e["type"] for e in events}
    assert "status" in types
    assert "done" in types

    statuses = [e["message"] for e in events if e["type"] == "status"]
    joined = " ".join(statuses)
    assert "分析" in joined  # supervisor
    assert "知识库" in joined  # rag

    done = [e for e in events if e["type"] == "done"]
    assert done
    assert done[0]["final_answer"]
    assert done[0]["citations"]

    # 最终回答应有 token 流式输出
    tokens = [e for e in events if e["type"] == "token"]
    assert tokens


def test_stream_tool_status(fresh_cache: None) -> None:
    cid = f"stream-{uuid.uuid4().hex[:8]}"
    events = list(stream_chat("查询订单 ORD20260001 的状态", conversation_id=cid))

    tool_events = [e for e in events if e["type"] == "tool"]
    assert tool_events
    assert any(e["name"] == "get_order" for e in tool_events)
