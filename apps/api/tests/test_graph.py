from app.llm import get_complete
from app.main import app
from app.services.bot import handoff_priority


def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def _conversation(client, headers):
    res = client.post("/api/conversations", headers=headers)
    assert res.status_code == 200
    return res.json()["conversation_id"]


def test_handoff_uses_ticket_fill_and_skips_resolve(client):
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        if prompt.startswith("根据用户消息输出两行"):
            return "category: shipping\nsummary: 用户要求人工"
        return "不应该出现的回答"

    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "转人工"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["assistant_message"] is None
    assert body["mode"] == "human"
    agent = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    ticket = client.get(f"/api/tickets/{body['ticket_id']}", headers=agent).json()["ticket"]
    assert ticket["priority"] == "normal"
    assert ticket["category"] == "shipping"
    assert ticket["summary"] == "用户要求人工"
    assert prompts
    assert all(not prompt.startswith("你是星辰数码商城客服") for prompt in prompts)


def test_explicit_refund_requests_are_high():
    for message in ("我要退款", "申请退款", "帮我退款", "给我退款"):
        assert handoff_priority(message) == "high"
    assert handoff_priority("退款多久到账") is None


def test_refund_arrival_question_stays_with_bot(client):
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "退款多久到账"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ticket_id"] is None
    assert body["mode"] == "bot"


def test_refund_keyword_is_high_priority(client):
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "我要退款"},
    )
    assert res.status_code == 200
    assert res.json()["assistant_message"] is None
    agent = {"Authorization": f"Bearer {_token(client, 'agent1')}"}
    ticket = client.get(f"/api/tickets/{res.json()['ticket_id']}", headers=agent).json()["ticket"]
    assert ticket["priority"] == "high"


def test_prefill_runs_only_when_refusals_open_a_ticket(client):
    prompts: list[str] = []

    def complete(prompt: str) -> str:
        prompts.append(prompt)
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 无法处理"
        return "知识库暂无依据"

    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    first = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "这个怎么退"},
    )
    assert first.json()["ticket_id"] is None
    second = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "那运费呢"},
    )
    assert second.json()["mode"] == "human"
    assert second.json()["assistant_message"] == "知识库暂无依据"
    prefill_calls = [prompt for prompt in prompts if prompt.startswith("根据用户消息输出两行")]
    assert len(prefill_calls) == 1


def test_longer_sentence_containing_refusal_does_not_open_ticket(client):
    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 无法处理"
        return "根据知识库暂无依据，我建议联系人工"

    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    for message in ("这个怎么退", "那运费呢"):
        res = client.post(
            "/api/chat",
            headers=headers,
            json={"conversation_id": cid, "message": message},
        )
        assert res.status_code == 200
        assert res.json()["ticket_id"] is None
        assert res.json()["mode"] == "bot"


def test_service_fallback_does_not_open_ticket(client):
    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 故障"
        raise RuntimeError("down")

    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    for message in ("运费谁承担", "那多久到"):
        res = client.post(
            "/api/chat",
            headers=headers,
            json={"conversation_id": cid, "message": message},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ticket_id"] is None
        assert body["mode"] == "bot"
        assert body["assistant_message"] == "抱歉，当前服务暂时不可用，请稍后再试。"


def test_lookup_order_id_is_remembered(client, db_session):
    from app.llm import ModelTurn, ToolCall
    from app.models import ConversationSlot

    state = {"used": False}

    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 订单"
        return "未调用工具"

    def with_tools(messages, tools):
        if not state["used"]:
            state["used"] = True
            return ModelTurn("", [ToolCall("lookup_orders", {})])
        return ModelTurn("键盘订单已发货", [])

    complete.with_tools = with_tools
    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    cid = _conversation(client, headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "帮我看 ORD12345678"},
    )
    assert res.status_code == 200
    assert res.json()["ticket_id"] is None
    db_session.expire_all()
    slot = db_session.get(ConversationSlot, cid)
    assert slot is not None
    assert slot.last_order_id == "ORD12345678"
