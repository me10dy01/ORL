"""会话用例：标题校验在这里，存储仍由 memory 完成。"""

from __future__ import annotations

from app import memory
from app.exceptions import ValidationError


def list_conversations() -> list[dict]:
    return memory.list_conversations()


def get_conversation(conversation_id: str, limit: int = 50) -> dict:
    return {
        "conversation_id": conversation_id,
        "messages": memory.get_recent_messages(conversation_id, limit=limit),
    }


def rename_conversation(conversation_id: str, title: str) -> str | None:
    """重命名成功返回清洗后的标题；会话不存在返回 None。"""
    cleaned = title.strip()
    if not cleaned:
        raise ValidationError("标题不能为空")
    if not memory.rename_conversation(conversation_id, cleaned):
        return None
    return cleaned


def delete_conversation(conversation_id: str) -> dict:
    return memory.delete_conversation(conversation_id)


def delete_conversations(conversation_ids: list[str]) -> dict:
    return memory.delete_conversations(conversation_ids)
