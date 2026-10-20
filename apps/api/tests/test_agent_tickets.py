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


def test_complaint_opens_high_priority_ticket(client):
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "我要投诉发货太慢"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "human"
    assert body["assistant_message"] is None
    agent = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    detail = client.get(f"/api/tickets/{body['ticket_id']}", headers=agent)
    assert detail.status_code == 200
    ticket = detail.json()["ticket"]
    assert ticket["priority"] == "high"
    assert ticket["category"] == "refund"
    assert ticket["summary"] == "用户需要帮助"


def test_llm_failure_still_opens_handoff_ticket(client):
    def boom(prompt: str) -> str:
        raise RuntimeError("down")

    app.dependency_overrides[get_complete] = lambda: boom
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "转人工"},
    )
    assert res.status_code == 200
    agent = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    detail = client.get(f"/api/tickets/{res.json()['ticket_id']}", headers=agent)
    ticket = detail.json()["ticket"]
    assert ticket["priority"] == "normal"
    assert ticket["category"] == "general"
    assert ticket["summary"] == "转人工"


def test_two_refusals_open_normal_ticket(client, messages_for):
    def refusing(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 无法处理"
        return "知识库暂无依据"

    app.dependency_overrides[get_complete] = lambda: refusing
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    first = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "这个怎么退"},
    )
    assert first.status_code == 200
    assert first.json()["mode"] == "bot"
    assert first.json()["ticket_id"] is None
    assert first.json()["assistant_message"] == "知识库暂无依据"

    second = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "那运费呢"},
    )
    assert second.status_code == 200
    body = second.json()
    assert body["mode"] == "human"
    assert body["assistant_message"] == "知识库暂无依据"
    agent = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    detail = client.get(f"/api/tickets/{body['ticket_id']}", headers=agent)
    assert detail.json()["ticket"]["priority"] == "normal"
    roles = [role for role, _content in messages_for(cid)]
    assert roles.count("assistant") == 2


def test_queue_orders_high_priority_first(client):
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    normal_id = _conversation(client, headers)
    client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": normal_id, "message": "转人工"},
    )
    complaint_id = _conversation(client, headers)
    client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": complaint_id, "message": "我要退款"},
    )
    agent = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    listed = client.get("/api/tickets?status=open", headers=agent)
    assert listed.status_code == 200
    priorities = [item["priority"] for item in listed.json()["tickets"]]
    assert priorities == ["high", "normal"]
    customer = client.get("/api/tickets?status=open", headers=headers)
    assert customer.status_code == 403


def test_agent_reply_then_resolve_and_close(client):
    user_headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    agent_headers = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    cid = _conversation(client, user_headers)
    opened = client.post(
        "/api/chat",
        headers=user_headers,
        json={"conversation_id": cid, "message": "转人工"},
    )
    ticket_id = opened.json()["ticket_id"]

    early = client.post(
        f"/api/tickets/{ticket_id}/messages",
        headers=agent_headers,
        json={"content": "先别回"},
    )
    assert early.status_code == 409

    claim = client.post(f"/api/tickets/{ticket_id}/claim", headers=agent_headers)
    assert claim.status_code == 200
    reply = client.post(
        f"/api/tickets/{ticket_id}/messages",
        headers=agent_headers,
        json={"content": "已为您处理"},
    )
    assert reply.status_code == 200

    forbidden = client.post(
        f"/api/tickets/{ticket_id}/transition",
        headers=user_headers,
        json={"action": "close"},
    )
    assert forbidden.status_code == 403

    resolved = client.post(
        f"/api/tickets/{ticket_id}/transition",
        headers=agent_headers,
        json={"action": "resolve"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"
    closed = client.post(
        f"/api/tickets/{ticket_id}/transition",
        headers=agent_headers,
        json={"action": "close"},
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    again = client.post(
        f"/api/tickets/{ticket_id}/transition",
        headers=agent_headers,
        json={"action": "close"},
    )
    assert again.status_code == 409

    detail = client.get(f"/api/tickets/{ticket_id}", headers=agent_headers)
    contents = [item["content"] for item in detail.json()["messages"]]
    assert "已为您处理" in contents
