from app.llm import ModelTurn, ToolCall, get_complete
from app.main import app
from app.services.tools import lookup_stock


def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def test_keyboard_question_returns_keyboard_stock_only(client, db_session):
    text = lookup_stock(db_session, "键盘还有货吗")
    assert "库存 12" in text
    assert "库存 20" not in text
    assert lookup_stock(db_session, "P001") == "商品 P001 星辰 K87 机械键盘，库存 12"
    assert lookup_stock(db_session, "  ") == "未找到"


def test_stock_query_error_returns_not_found():
    class Broken:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("down")

        def query(self, *_args, **_kwargs):
            raise RuntimeError("down")

    assert lookup_stock(Broken(), "P001") == "未找到"


def test_chat_stock_answer_uses_public_stock(client):
    seen: list[str] = []
    state = {"used": False}

    def complete(prompt: str) -> str:
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 库存"
        return "未查到"

    def with_tools(messages, tools):
        seen.append(repr(messages))
        names = [tool["function"]["name"] for tool in tools]
        assert "lookup_stock" in names
        stock = next(tool for tool in tools if tool["function"]["name"] == "lookup_stock")
        assert "user_id" not in stock["function"]["parameters"].get("properties", {})
        if not state["used"]:
            state["used"] = True
            return ModelTurn("", [ToolCall("lookup_stock", {"query": "键盘还有货吗"})])
        return ModelTurn("商品 P001 星辰 K87 机械键盘，库存 12", [])

    complete.with_tools = with_tools
    app.dependency_overrides[get_complete] = lambda: complete
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": conv.json()["conversation_id"], "message": "键盘还有货吗"},
    )
    assert res.status_code == 200
    assert "库存 12" in res.json()["assistant_message"]
    assert "库存 20" not in res.json()["assistant_message"]
    assert any("库存 12" in item for item in seen)
    assert all("库存 20" not in item for item in seen)
