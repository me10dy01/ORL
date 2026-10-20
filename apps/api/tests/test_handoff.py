def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def test_handoff_creates_open_ticket(client, messages_for):
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
    assert body["assistant_message"] is None
    assert [item for item in messages_for(cid) if item[0] == "user"] == [("user", "转人工")]


def test_overlapping_claims_only_one_succeeds(client):
    import threading
    import time

    from app.domain.tickets import claim_if_open
    from app.models import User
    from tests.conftest import SessionLocal

    user_headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=user_headers)
    chat = client.post(
        "/api/chat",
        headers=user_headers,
        json={"conversation_id": conv.json()["conversation_id"], "message": "转人工"},
    )
    tid = chat.json()["ticket_id"]
    setup = SessionLocal()
    agent_id = setup.query(User).filter(User.username == "agent1").one().id
    setup.close()

    first = SessionLocal()
    second = SessionLocal()
    try:
        assert claim_if_open(first, tid, agent_id) is True
        outcome: dict[str, bool] = {}

        def loser():
            outcome["ok"] = claim_if_open(second, tid, agent_id)

        thread = threading.Thread(target=loser)
        thread.start()
        time.sleep(0.5)
        first.commit()
        thread.join(5)
        assert outcome["ok"] is False
        second.rollback()
    finally:
        first.close()
        second.close()


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


def test_close_returns_conversation_to_bot(client, messages_for):
    user_headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    agent_headers = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    conv = client.post("/api/conversations", headers=user_headers)
    cid = conv.json()["conversation_id"]

    chat = client.post("/api/chat", headers=user_headers, json={"conversation_id": cid, "message": "转人工"})
    tid = chat.json()["ticket_id"]
    client.post(f"/api/tickets/{tid}/claim", headers=agent_headers)
    client.post(f"/api/tickets/{tid}/transition", headers=agent_headers, json={"action": "resolve"})
    client.post(f"/api/tickets/{tid}/transition", headers=agent_headers, json={"action": "close"})

    follow = client.post(
        "/api/chat",
        headers=user_headers,
        json={"conversation_id": cid, "message": "你好"},
    )
    assert follow.status_code == 200
    body = follow.json()
    assert body["mode"] == "bot"
    assert body["ticket_id"] is None
    contents = [content for _, content in messages_for(cid) if _ == "system"]
    assert any(item.startswith("人工已处理：") for item in contents)


def test_bot_returns_rewritten_query(client, messages_for):
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
    assert body["assistant_message"] == "这是一条普通回复"
    assert ("user", "运费谁承担") in messages_for(cid)
    assert ("assistant", "这是一条普通回复") in messages_for(cid)
