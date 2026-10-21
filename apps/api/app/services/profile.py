"""用户画像：跨会话记忆——姓名/手机/最近商品/上一张工单摘要。

与会话槽位（ConversationSlot）的区别：槽位随会话走，画像随用户走。
画像注入 prompt（见 bot.profile_line），让模型在新会话里也能认出老顾客。
"""

from sqlalchemy.orm import Session

from app.models import UserProfile


def get_profile(db: Session, user_id: str) -> UserProfile | None:
    return db.get(UserProfile, user_id)


def remember_profile(
    db: Session,
    user_id: str,
    *,
    name: str | None = None,
    phone: str | None = None,
    last_product_id: str | None = None,
    last_ticket_summary: str | None = None,
) -> UserProfile:
    """upsert 画像。只写非 None 字段——没新信息时不擦除旧值。"""
    row = db.get(UserProfile, user_id)
    if row is None:
        row = UserProfile(user_id=user_id)
        db.add(row)
    if name:
        row.name = name
    if phone:
        row.phone = phone
    if last_product_id:
        row.last_product_id = last_product_id
    if last_ticket_summary:
        row.last_ticket_summary = last_ticket_summary
    return row
