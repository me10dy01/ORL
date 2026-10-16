import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.db import Base, get_db
from app.main import app
from app.models import seed_users


TEST_DATABASE_URL = "postgresql+psycopg://orl:orl@127.0.0.1:5432/orl"


engine = create_engine(TEST_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def client():
    test_settings = Settings(database_url=TEST_DATABASE_URL)

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

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed_users(db)
    db.close()

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    db = SessionLocal()
    # Delete child rows before parents to avoid foreign-key errors.
    db.execute(text("DELETE FROM ticket_events"))
    db.execute(text("DELETE FROM tickets"))
    db.execute(text("DELETE FROM messages"))
    db.execute(text("DELETE FROM conversation_slots"))
    db.execute(text("DELETE FROM conversations"))
    db.execute(text("DELETE FROM users"))
    db.commit()
    db.close()
    Base.metadata.drop_all(bind=engine)


def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def test_handoff_creates_open_ticket(client):
    token = _token(client, "user1")
    headers = {"Authorization": f"Bearer {token}"}
    conv = client.post("/api/conversations", headers=headers)
    assert conv.status_code == 200
    cid = conv.json()["conversation_id"]

    res = client.post("/api/chat", headers=headers, json={"conversation_id": cid, "message": "转人工"})
    assert res.status_code == 200
    body = res.json()
    assert body["ticket_id"]
    assert body["mode"] == "human"
    assert body["rewritten_query"] is None


def test_claim_flow(client):
    user_token = _token(client, "user1")
    agent_token = _token(client, "agent1")
    user_headers = {"Authorization": f"Bearer {user_token}"}
    agent_headers = {"Authorization": f"Bearer {agent_token}"}

    conv = client.post("/api/conversations", headers=user_headers)
    cid = conv.json()["conversation_id"]

    chat = client.post("/api/chat", headers=user_headers, json={"conversation_id": cid, "message": "转人工"})
    tid = chat.json()["ticket_id"]

    # Customer cannot claim.
    forbid = client.post(f"/api/tickets/{tid}/claim", headers=user_headers)
    assert forbid.status_code == 403

    # Agent claims successfully.
    claim = client.post(f"/api/tickets/{tid}/claim", headers=agent_headers)
    assert claim.status_code == 200
    assert claim.json()["status"] == "in_progress"

    # Second claim is a conflict.
    conflict = client.post(f"/api/tickets/{tid}/claim", headers=agent_headers)
    assert conflict.status_code == 409


def test_bot_returns_rewritten_query(client):
    token = _token(client, "user1")
    headers = {"Authorization": f"Bearer {token}"}

    conv = client.post("/api/conversations", headers=headers)
    cid = conv.json()["conversation_id"]

    res = client.post("/api/chat", headers=headers, json={"conversation_id": cid, "message": "运费谁承担"})
    assert res.status_code == 200
    body = res.json()
    assert body["ticket_id"] is None
    assert body["mode"] == "bot"
    assert body["rewritten_query"] == "运费谁承担"
