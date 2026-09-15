"""Phase 09 验收测试：Conversation Memory 跨请求存取。"""

from __future__ import annotations

import uuid

import pytest

from app import memory

pytestmark = pytest.mark.usefixtures("initialized_db")


def _cid() -> str:
    return f"mem-{uuid.uuid4().hex[:8]}"


def test_memory_add_and_get() -> None:
    cid = _cid()
    memory.add_message(cid, "user", "你好")
    memory.add_message(cid, "assistant", "你好，有什么可以帮你？")
    msgs = memory.get_recent_messages(cid, limit=10)
    assert [m["content"] for m in msgs] == ["你好", "你好，有什么可以帮你？"]


def test_memory_limit_and_order() -> None:
    cid = _cid()
    for i in range(5):
        memory.add_message(cid, "user", f"msg{i}")
    msgs = memory.get_recent_messages(cid, limit=3)
    assert len(msgs) == 3
    assert msgs[0]["content"] == "msg2"
    assert msgs[-1]["content"] == "msg4"


def test_list_conversations_sorted_by_latest() -> None:
    """会话列表按最近消息时间倒序（同秒以 id 兜底）。"""
    cid_old = _cid()
    memory.add_message(cid_old, "user", "较早的消息")
    cid_new = _cid()
    memory.add_message(cid_new, "user", "较晚的消息")

    rows = memory.list_conversations()
    ids = [r["conversation_id"] for r in rows]
    assert cid_new in ids and cid_old in ids
    assert ids.index(cid_new) < ids.index(cid_old)


def test_rename_conversation() -> None:
    cid = _cid()
    memory.add_message(cid, "user", "你好")
    assert memory.rename_conversation(cid, "退货咨询") is True
    row = next(c for c in memory.list_conversations() if c["conversation_id"] == cid)
    assert row["title"] == "退货咨询"


def test_rename_missing_conversation_returns_false() -> None:
    assert memory.rename_conversation("mem-not-exist-xyz", "x") is False


def test_delete_conversation() -> None:
    cid = _cid()
    memory.add_message(cid, "user", "a")
    memory.add_message(cid, "assistant", "b")
    result = memory.delete_conversation(cid)
    assert result["deleted_messages"] == 2
    assert not any(c["conversation_id"] == cid for c in memory.list_conversations())
    assert memory.get_recent_messages(cid) == []
