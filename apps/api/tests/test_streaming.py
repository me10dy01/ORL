import json

from app.llm import ModelTurn, ToolCall, get_complete
from app.main import app


def _events(response) -> list[dict]:
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def test_stream_tokens_match_final_answer(client):
    def reply(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 运费"
        return "这是一条足够长的普通客服回复，用来检查流式切段是否拼得回去。"

    app.dependency_overrides[get_complete] = lambda: reply
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat/stream",
        headers=headers,
        json={"conversation_id": conv.json()["conversation_id"], "message": "退货运费谁承担"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _events(res)
    tokens = [event["content"] for event in events if event["type"] == "token"]
    done = next(event for event in events if event["type"] == "done")
    assert "".join(tokens) == done["final_answer"]
    assert done["final_answer"].startswith("这是一条足够长")
    assert events[0]["type"] == "meta"


def test_handoff_stream_has_no_tokens_before_done(client):
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat/stream",
        headers=headers,
        json={"conversation_id": conv.json()["conversation_id"], "message": "转人工"},
    )
    events = _events(res)
    types = [event["type"] for event in events]
    assert "ticket_created" in types
    assert types.index("ticket_created") < types.index("done")
    assert "token" not in types
    done = next(event for event in events if event["type"] == "done")
    assert done["final_answer"] == "已转接人工客服"
    assert done["ticket_id"]


def test_stream_sends_model_chunks_and_drops_tool_draft(client):
    seen: list[str] = []
    state = {"used": False}

    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 库存"
        return "库存充足"

    def with_stream(messages, tools):
        seen.append(repr(messages))
        if not state["used"]:
            state["used"] = True
            yield ModelTurn("草稿", [])
            yield ModelTurn("", [ToolCall("lookup_stock", {"query": "键盘还有货吗"})])
            return
        yield ModelTurn("键盘", [])
        yield ModelTurn("还有 12 件", [])

    complete.with_stream = with_stream
    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat/stream",
        headers=headers,
        json={"conversation_id": conv.json()["conversation_id"], "message": "键盘还有货吗"},
    )
    assert res.status_code == 200
    events = _events(res)
    parts: list[str] = []
    for event in events:
        if event["type"] == "reset":
            parts.clear()
        elif event["type"] == "token":
            parts.append(event["content"])
    done = next(event for event in events if event["type"] == "done")
    assert "".join(parts) == done["final_answer"]
    assert done["final_answer"] == "键盘还有 12 件"
    assert "草稿" not in done["final_answer"]
    assert any(event.get("message") == "正在查库存" for event in events)
    assert events.index(next(event for event in events if event["type"] == "reset")) < events.index(done)
    assert any("库存 12" in item for item in seen)
