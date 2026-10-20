import uuid
from datetime import datetime, timezone
from typing import Optional

from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from pgvector.sqlalchemy import Vector
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import EMBEDDING_DIM
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
    last_phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    candidate_order_ids: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="normal")
    category: Mapped[str] = mapped_column(String, nullable=False, default="general")
    summary: Mapped[str] = mapped_column(String, nullable=False, default="")
    suggested_reply: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    assignee_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_product_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_ticket_summary: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_name: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    product_id: Mapped[Optional[str]] = mapped_column(ForeignKey("products.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


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


def _upsert_product(db, product_id: str, name: str, stock: int) -> None:
    row = db.get(Product, product_id)
    if row is None:
        db.add(Product(id=product_id, name=name, stock=stock))
        return
    row.name = name
    row.stock = stock


def _upsert_order(db, order_id: str, **fields) -> None:
    row = db.get(OrderRecord, order_id)
    if row is None:
        db.add(OrderRecord(id=order_id, **fields))
        return
    for key, value in fields.items():
        setattr(row, key, value)


def seed_catalog(db) -> None:
    user = db.query(User).filter(User.username == "user1").one()
    _upsert_product(db, "P001", "星辰 K87 机械键盘", 12)
    _upsert_product(db, "P002", "星辰鼠标", 20)
    db.flush()
    _upsert_order(
        db,
        "ORD12345678",
        user_id=user.id,
        phone="13800001111",
        customer_name="张三",
        product_id="P001",
        status="已发货",
        amount=Decimal("299.00"),
        created_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
    )
    _upsert_order(
        db,
        "ORD12345679",
        user_id=user.id,
        phone="13800002222",
        customer_name="张三",
        product_id="P002",
        status="待付款",
        amount=Decimal("99.00"),
        created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    _upsert_order(
        db,
        "ORD87654321",
        user_id=None,
        phone="13800001111",
        customer_name="李四",
        product_id="P002",
        status="已签收",
        amount=Decimal("199.00"),
        created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    db.commit()
