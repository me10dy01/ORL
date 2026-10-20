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
