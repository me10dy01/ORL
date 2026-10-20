import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.db import Base, ensure_vector, get_db, reset_engine
from app.llm import get_complete, get_embed
from app.main import app
from app.models import Message, seed_users


TEST_DATABASE_URL = "postgresql+psycopg://orl:orl@127.0.0.1:5432/orl_test"


def _ensure_test_db():
    default_engine = create_engine(
        "postgresql+psycopg://orl:orl@127.0.0.1:5432/orl",
        isolation_level="AUTOCOMMIT",
    )
    with default_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname='orl_test'")
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE orl_test"))
    default_engine.dispose()


_ensure_test_db()

engine = create_engine(TEST_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def default_complete(prompt: str) -> str:
    if prompt.startswith("根据用户消息输出两行"):
        return "category: refund\nsummary: 用户需要帮助"
    return "这是一条普通回复"


def default_embed(text: str) -> list[float]:
    return [0.0] * 1024


@pytest.fixture
def client():
    test_settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret="test-secret-must-be-at-least-32-bytes-long",
    )

    def override_get_settings():
        return test_settings

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_settings] = override_get_settings
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_complete] = lambda: default_complete
    app.dependency_overrides[get_embed] = lambda: default_embed

    reset_engine(TEST_DATABASE_URL)
    ensure_vector(engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed_users(db)
    db.close()

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    reset_engine()
    db = SessionLocal()
    db.execute(text("DELETE FROM ticket_events"))
    db.execute(text("DELETE FROM tickets"))
    db.execute(text("DELETE FROM messages"))
    db.execute(text("DELETE FROM conversation_slots"))
    db.execute(text("DELETE FROM orders"))
    db.execute(text("DELETE FROM conversations"))
    db.execute(text("DELETE FROM user_profiles"))
    db.execute(text("DELETE FROM users"))
    db.execute(text("DELETE FROM products"))
    db.execute(text("DELETE FROM knowledge_chunks"))
    db.commit()
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def messages_for():
    def _load(conversation_id: str) -> list[tuple[str, str]]:
        db = SessionLocal()
        try:
            rows = (
                db.query(Message)
                .filter(Message.conversation_id == conversation_id)
                .order_by(Message.created_at)
                .all()
            )
            return [(row.role, row.content) for row in rows]
        finally:
            db.close()

    return _load
