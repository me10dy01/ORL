from app.models import User, password_hash


def _token(client, username):
    res = client.post(
        "/api/auth/login", json={"username": username, "password": "demo1234"}
    )
    assert res.status_code == 200
    return res.json()["access_token"]


def test_owner_can_list_and_read_messages(client):
    token = _token(client, "user1")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/conversations", headers=headers)
    cid = created.json()["conversation_id"]
    client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "退货运费谁承担"},
    )

    listed = client.get("/api/conversations/me", headers=headers)
    assert listed.status_code == 200
    item = next(row for row in listed.json()["conversations"] if row["conversation_id"] == cid)
    assert item["mode"] == "bot"
    assert item["preview"] == "这是一条普通回复"

    detail = client.get(f"/api/conversations/{cid}", headers=headers)
    assert detail.status_code == 200
    contents = [item["content"] for item in detail.json()["messages"]]
    assert "退货运费谁承担" in contents


def test_conversation_preview_truncates_long_reply(client):
    from app.llm import get_complete
    from app.main import app

    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 很长"
        return "这是一段超过四十个字的客服回复，用来检查会话列表只保留前面一小段预览文字，后面这些字应该被截掉。"

    app.dependency_overrides[get_complete] = lambda: complete
    token = _token(client, "user1")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/conversations", headers=headers)
    cid = created.json()["conversation_id"]
    client.post("/api/chat", headers=headers, json={"conversation_id": cid, "message": "预览"})
    listed = client.get("/api/conversations/me", headers=headers)
    item = next(row for row in listed.json()["conversations"] if row["conversation_id"] == cid)
    assert len(item["preview"]) == 41
    assert item["preview"].endswith("…")
    assert item["preview"].startswith("这是一段超过四十个字")


def test_customer_cannot_read_another_conversation(client, db_session):
    other = User(
        username="user2",
        role="customer",
        password_hash=password_hash.hash("demo1234"),
    )
    db_session.add(other)
    db_session.commit()

    other_token = _token(client, "user2")
    created = client.post(
        "/api/conversations",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    cid = created.json()["conversation_id"]

    user_token = _token(client, "user1")
    denied = client.get(
        f"/api/conversations/{cid}",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert denied.status_code == 404
