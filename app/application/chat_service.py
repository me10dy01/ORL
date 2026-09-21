"""聊天用例服务。

API 层只负责 HTTP/SSE 协议；会话 ID、工作流调用和返回结果组装在此处集中，
供 HTTP、CLI 或后续任务队列复用。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from app.agents.main_graph import run_main_graph
from app.streaming import stream_chat


def new_conversation_id() -> str:
    """创建不依赖传输协议的会话 ID。"""
    return f"conv-{uuid.uuid4().hex[:8]}"


def run_chat(message: str, conversation_id: str | None = None) -> tuple[str, dict]:
    """执行一次非流式聊天，返回确定的会话 ID 与工作流结果。"""
    cid = conversation_id or new_conversation_id()
    return cid, run_main_graph(message, conversation_id=cid)


def stream_chat_events(
    message: str, conversation_id: str | None = None
) -> tuple[str, Iterator[dict]]:
    """执行一次流式聊天；调用方负责把事件编码为 SSE。"""
    cid = conversation_id or new_conversation_id()
    return cid, stream_chat(message, conversation_id=cid)
