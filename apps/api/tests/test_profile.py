from app.llm import get_complete
from app.main import app
from app.models import UserProfile
from app.services.bot import profile_line


def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def test_profile_line_empty_when_no_profile():
    assert profile_line(None) == ""


def test_profile_saved_from_slots_and_shown_in_prompt(client, db_session):
    user_headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    agent_headers = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    conv = client.post("/api/conversations", headers=user_headers)
    cid = conv.json()["conversation_id"]

    client.post(
        "/api/chat",
        headers=user_headers,
        json={"conversation_id": cid, "message": "我叫张三，手机13800001111，订单 ORD12345678"},
    )
    chat = client.post("/api/chat", headers=user_headers, json={"conversation_id": cid, "message": "转人工"})
    tid = chat.json()["ticket_id"]
    client.post(f"/api/tickets/{tid}/claim", headers=agent_headers)
    client.post(f"/api/tickets/{tid}/transition", headers=agent_headers, json={"action": "resolve"})
    client.post(f"/api/tickets/{tid}/transition", headers=agent_headers, json={"action": "close"})

    profile = db_session.query(UserProfile).filter(UserProfile.user_id.isnot(None)).first()
    assert profile is not None
    assert profile.name == "张三"
    assert profile.phone == "13800001111"
    assert profile.last_ticket_summary

    seen: list[str] = []

    def with_tools(messages, tools):
        seen.append(repr(messages))
        from app.llm import ModelTurn

        return ModelTurn("这是一条普通回复", [])

    def complete(prompt: str) -> str:
        return "这是一条普通回复"

    complete.with_tools = with_tools
    app.dependency_overrides[get_complete] = lambda: complete

    res = client.post(
        "/api/chat",
        headers=user_headers,
        json={"conversation_id": cid, "message": "你好"},
    )
    assert res.status_code == 200
    assert seen
    first = seen[0]
    assert "已知顾客" in first
    assert "张三" in first


def test_ticket_detail_includes_profile(client):
    user_headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    agent_headers = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    conv = client.post("/api/conversations", headers=user_headers)
    cid = conv.json()["conversation_id"]
    client.post(
        "/api/chat",
        headers=user_headers,
        json={"conversation_id": cid, "message": "我叫张三，手机13800001111"},
    )
    chat = client.post("/api/chat", headers=user_headers, json={"conversation_id": cid, "message": "转人工"})
    tid = chat.json()["ticket_id"]

    detail = client.get(f"/api/tickets/{tid}", headers=agent_headers)
    assert detail.status_code == 200
    profile = detail.json()["profile"]
    assert profile["name"] == "张三"
    assert profile["phone"] == "13800001111"
