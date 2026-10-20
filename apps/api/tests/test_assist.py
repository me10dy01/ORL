from app.llm import get_complete
from app.main import app


def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def _conversation(client, headers):
    res = client.post("/api/conversations", headers=headers)
    assert res.status_code == 200
    return res.json()["conversation_id"]


def test_handoff_opens_ticket_without_suggested_reply(client, messages_for):
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        if prompt.startswith("根据用户消息输出两行"):
            return "category: shipping\nsummary: 用户要求人工"
        return "不应该出现的回答"

    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    opened = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "转人工"},
    )
    assert opened.status_code == 200
    assert opened.json()["ticket_id"]
    agent = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    detail = client.get(f"/api/tickets/{opened.json()['ticket_id']}", headers=agent)
    assert detail.status_code == 200
    assert "suggested_reply" not in detail.json()["ticket"]
    assert all(not item.startswith("你是坐席助手") for item in prompts)
    assert [item for item in messages_for(cid) if item[0] == "agent"] == []


def test_agent_sends_typed_reply(client, messages_for):
    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 转人工"
        return "普通回复"

    app.dependency_overrides[get_complete] = lambda: complete
    user_headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    agent_headers = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    cid = _conversation(client, user_headers)
    opened = client.post(
        "/api/chat",
        headers=user_headers,
        json={"conversation_id": cid, "message": "转人工"},
    )
    ticket_id = opened.json()["ticket_id"]
    assert client.post(f"/api/tickets/{ticket_id}/claim", headers=agent_headers).status_code == 200
    sent = client.post(
        f"/api/tickets/{ticket_id}/messages",
        headers=agent_headers,
        json={"content": "我改过的回复"},
    )
    assert sent.status_code == 200
    assert ("agent", "我改过的回复") in messages_for(cid)
