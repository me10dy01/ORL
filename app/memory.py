"""对话记忆（Conversation Memory，Phase 09 / Phase 15 扩展）。

跨请求保存历史对话上下文，与 Workflow State 分离（AGENTS.md 第 14 条）：
- 通过 conversation_id 关联
- 保留最近消息，通过 limit 控制上下文长度
- 会话元信息（标题）单独存 conversations 表，支持重命名 / 删除
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import db
from app.exceptions import NotFoundError

DEFAULT_LIMIT = 10
logger = logging.getLogger(__name__)


def _ensure_conversation(session: Session, conversation_id: str) -> None:
    """确保会话元信息存在（幂等）。"""
    if session.get(db.Conversation, conversation_id) is None:
        session.add(db.Conversation(conversation_id=conversation_id, title=""))


def add_message(conversation_id: str, role: str, content: str) -> None:
    """追加一条消息（并确保会话存在）。"""
    try:
        with db.session_scope() as session:
            _ensure_conversation(session, conversation_id)
            session.add(
                db.Message(conversation_id=conversation_id, role=role, content=content)
            )
    except SQLAlchemyError:
        logger.warning("消息写入失败（MySQL 不可用）", exc_info=True)


def get_recent_messages(conversation_id: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """按时间顺序返回最近 limit 条消息（旧→新）。"""
    try:
        with db.session_scope() as session:
            stmt = (
                select(db.Message)
                .where(db.Message.conversation_id == conversation_id)
                .order_by(db.Message.id.desc())
                .limit(limit)
            )
            rows = list(session.scalars(stmt))
            rows.reverse()
            return [{"role": r.role, "content": r.content} for r in rows]
    except SQLAlchemyError:
        logger.warning("消息读取失败（MySQL 不可用）", exc_info=True)
        return []


def list_conversations() -> list[dict]:
    """列出全部会话（最近消息时间倒序），含标题与消息数。"""
    try:
        with db.session_scope() as session:
            stmt = (
                select(
                    db.Message.conversation_id,
                    func.count(db.Message.id),
                    func.max(db.Message.created_at),
                    func.max(db.Conversation.title),
                )
                .join(
                    db.Conversation,
                    db.Conversation.conversation_id == db.Message.conversation_id,
                    isouter=True,
                )
                .group_by(db.Message.conversation_id)
                .order_by(
                    func.max(db.Message.created_at).desc(),
                    func.max(db.Message.id).desc(),
                )
            )
            rows = session.execute(stmt).all()
            return [
                {
                    "conversation_id": row[0],
                    "message_count": row[1],
                    "last_message_at": row[2].isoformat() if row[2] else None,
                    "title": row[3] or "",
                }
                for row in rows
            ]
    except SQLAlchemyError:
        logger.warning("会话列表查询失败（MySQL 不可用）", exc_info=True)
        return []


def rename_conversation(conversation_id: str, title: str) -> bool:
    """重命名会话；会话不存在时返回 False。"""
    try:
        with db.session_scope() as session:
            conversation = session.get(db.Conversation, conversation_id)
            if conversation is None:
                return False
            conversation.title = title
            return True
    except SQLAlchemyError:
        logger.warning("会话重命名失败（MySQL 不可用）", exc_info=True)
        return False


def delete_conversation(conversation_id: str) -> dict:
    """删除会话及其全部消息。"""
    try:
        with db.session_scope() as session:
            if session.get(db.Conversation, conversation_id) is None:
                raise NotFoundError(f"会话不存在: {conversation_id}")
            deleted = session.scalar(
                select(func.count())
                .select_from(db.Message)
                .where(db.Message.conversation_id == conversation_id)
            )
            session.execute(
                delete(db.Message).where(db.Message.conversation_id == conversation_id)
            )
            conversation = session.get(db.Conversation, conversation_id)
            if conversation is not None:
                session.delete(conversation)
            return {
                "conversation_id": conversation_id,
                "deleted_messages": deleted or 0,
            }
    except NotFoundError:
        raise
    except SQLAlchemyError:
        logger.warning("会话删除失败（MySQL 不可用）", exc_info=True)
        return {"conversation_id": conversation_id, "deleted_messages": 0}


def delete_conversations(conversation_ids: list[str]) -> dict:
    """批量删除多个会话及其全部消息（用于列表"全选删除"）。"""
    if not conversation_ids:
        return {"deleted_conversations": 0, "deleted_messages": 0}
    try:
        with db.session_scope() as session:
            deleted = session.scalar(
                select(func.count())
                .select_from(db.Message)
                .where(db.Message.conversation_id.in_(conversation_ids))
            )
            session.execute(
                delete(db.Message).where(db.Message.conversation_id.in_(conversation_ids))
            )
            session.execute(
                delete(db.Conversation).where(
                    db.Conversation.conversation_id.in_(conversation_ids)
                )
            )
            return {
                "deleted_conversations": len(conversation_ids),
                "deleted_messages": deleted or 0,
            }
    except SQLAlchemyError:
        logger.warning("批量删除失败（MySQL 不可用）", exc_info=True)
        return {
            "deleted_conversations": len(conversation_ids),
            "deleted_messages": 0,
        }
