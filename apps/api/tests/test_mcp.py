from app.llm import ModelTurn, ToolCall, get_complete, get_embed
from app.main import app
from app.mcp_client import list_tool_names
from app.mcp_server import mcp, tool_request
import asyncio


def test_mcp_exposes_search_and_lookup():
    names = list_tool_names()
    assert "search_knowledge" in names
    assert "lookup_orders" in names
    assert "lookup_stock" in names


def test_lookup_without_logged_in_user_is_refused():
    assert tool_request.get() is None

    async def call():
        return await mcp.call_tool("lookup_orders", {"user_id": "someone-else"})

    result = asyncio.run(call())
    assert result.structured_content["text"] == "未找到"


def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def test_model_search_result_is_sent_back(client, db_session):
    from app.models import KnowledgeChunk

    vector = [1.0] + [0.0] * 1023
    db_session.add(
        KnowledgeChunk(
            document_name="退货运费.md",
            content="七天无理由退货的运费由买家承担",
            embedding=vector,
        )
    )
    db_session.commit()
    seen: list[str] = []
    state = {"used": False}

    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 运费"
        return "运费由买家承担"

    def with_tools(messages, tools):
        seen.append(repr(messages))
        names = [tool["function"]["name"] for tool in tools]
        assert "search_knowledge" in names
        assert "lookup_orders" in names
        lookup = next(tool for tool in tools if tool["function"]["name"] == "lookup_orders")
        assert "user_id" not in lookup["function"]["parameters"].get("properties", {})
        if not state["used"]:
            state["used"] = True
            return ModelTurn("", [ToolCall("search_knowledge", {"query": "退货运费"})])
        return ModelTurn("运费由买家承担", [])

    complete.with_tools = with_tools

    def fake_embed(text: str) -> list[float]:
        return vector

    app.dependency_overrides[get_complete] = lambda: complete
    app.dependency_overrides[get_embed] = lambda: fake_embed
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": conv.json()["conversation_id"], "message": "退货运费谁承担"},
    )
    assert res.status_code == 200
    assert res.json()["assistant_message"] == "运费由买家承担"
    assert any("买家承担" in item for item in seen)
    first = seen[0]
    assert "没有检索到知识" not in first
    assert "知识库暂无依据" not in first
    assert "search_knowledge" in first
    assert "lookup_orders" in first
    assert "lookup_stock" in first
    assert "七天无理由退货的运费由买家承担" in seen[1]
    assert "知识库暂无依据" in seen[1]


def test_model_cannot_impersonate_another_user(client):
    seen: list[str] = []
    state = {"used": False}

    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 订单"
        return "这是您的订单"

    def with_tools(messages, tools):
        seen.append(repr(messages))
        if not state["used"]:
            state["used"] = True
            return ModelTurn("", [ToolCall("lookup_orders", {"user_id": "intruder"})])
        return ModelTurn("这是您的订单", [])

    complete.with_tools = with_tools
    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": conv.json()["conversation_id"], "message": "我的订单到哪了"},
    )
    assert res.status_code == 200
    assert any("ORD12345678" in item for item in seen)
    assert all("199.00" not in item for item in seen)


def test_http_jwt_ignores_caller_user_and_slots(client, db_session):
    from app.config import Settings
    from app.mcp_server import mcp_http_user, user_id_from_authorization
    from app.models import User

    settings = Settings(
        database_url="postgresql+psycopg://orl:orl@127.0.0.1:5432/orl_test",
        jwt_secret="test-secret-must-be-at-least-32-bytes-long",
    )
    token = _token(client, "user1")
    user_id = user_id_from_authorization(f"Bearer {token}", db_session, settings)
    user = db_session.query(User).filter(User.username == "user1").one()
    assert user_id == user.id
    assert user_id_from_authorization("Bearer not-a-jwt", db_session, settings) == ""
    context = mcp_http_user.set(user_id)
    try:
        result = asyncio.run(
            mcp.call_tool(
                "lookup_orders",
                {
                    "user_id": "intruder",
                    "message": "我的订单到哪了",
                    "slots": {"last_order_id": "ORD87654321"},
                },
            )
        )
    finally:
        mcp_http_user.reset(context)
    text = result.structured_content["text"]
    assert "ORD12345678" in text
    assert "199.00" not in text
