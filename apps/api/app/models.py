import uuid
from datetime import datetime, timezone
from typing import Optional

from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

password_hash = PasswordHash((BcryptHasher(),))


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="bot")

    customer: Mapped["User"] = relationship("User")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


class ConversationSlot(Base):
    __tablename__ = "conversation_slots"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id"), primary_key=True
    )
    last_order_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_product_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="normal")
    category: Mapped[str] = mapped_column(String, nullable=False, default="general")
    summary: Mapped[str] = mapped_column(String, nullable=False, default="")
    assignee_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    to_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)


def seed_users(db) -> None:
    existing = {u for u, in db.query(User.username).all()}
    if "user1" not in existing:
        db.add(
            User(
                username="user1",
                role="customer",
                password_hash=password_hash.hash("demo1234"),
            )
        )
    if "agent1" not in existing:
        db.add(
            User(
                username="agent1",
                role="agent",
                password_hash=password_hash.hash("demo1234"),
            )
        )
    db.commit()
