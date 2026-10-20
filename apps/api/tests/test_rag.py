from app.llm import ModelTurn, ToolCall, get_complete, get_embed
from app.main import app
from app.models import KnowledgeChunk
from app.services.rag import chunk_text, ingest_documents

RETURN_VECTOR = [1.0] + [0.0] * 1023
SHIP_VECTOR = [0.0, 1.0] + [0.0] * 1022


def test_chunk_text_overlaps_long_document():
    chunks = chunk_text("字" * 600, size=500, overlap=50)
    assert chunks == ["字" * 500, "字" * 150]


def test_chunk_text_splits_two_headings_without_mixing():
    text = "## 退货范围\n\n买家可退。\n\n## 退货运费\n\n运费由买家承担。\n"
    chunks = chunk_text(text, size=500, overlap=50)
    assert chunks == ["## 退货范围\n买家可退。", "## 退货运费\n运费由买家承担。"]
    assert "买家可退" not in chunks[1]


def test_chunk_text_window_stays_inside_heading():
    text = "## 甲\n\n" + ("甲" * 600) + "\n\n## 乙\n\n乙节正文\n"
    chunks = chunk_text(text, size=500, overlap=50)
    assert len(chunks) >= 2
    assert all(chunk.startswith("## 甲\n") or chunk.startswith("## 乙\n") for chunk in chunks)
    jia = [chunk for chunk in chunks if chunk.startswith("## 甲\n")]
    yi = [chunk for chunk in chunks if chunk.startswith("## 乙\n")]
    assert yi == ["## 乙\n乙节正文"]
    assert all("乙节正文" not in chunk for chunk in jia)
    assert all("## 乙" not in chunk for chunk in jia)


def _token(client, username):
    res = client.post("/api/auth/login", json={"username": username, "password": "demo1234"})
    assert res.status_code == 200
    return res.json()["access_token"]


def _tool_model(seen: list[str], tool_name: str, final: str, arguments: dict | None = None):
    state = {"used": False}

    def complete(prompt: str) -> str:
        seen.append(prompt)
        if prompt.startswith("根据用户消息输出两行"):
            return "category: general\nsummary: 订单"
        return final

    def with_tools(messages, tools):
        seen.append(repr(messages))
        if not state["used"]:
            state["used"] = True
            return ModelTurn("", [ToolCall(tool_name, arguments or {})])
        return ModelTurn(final, [])

    complete.with_tools = with_tools
    return complete


def test_ingest_drops_chunks_for_removed_files(client, db_session, tmp_path):
    db_session.add(
        KnowledgeChunk(
            document_name="退货运费.md",
            content="旧运费",
            embedding=[0.0] * 1024,
        )
    )
    db_session.add(
        KnowledgeChunk(
            document_name="配送时效.md",
            content="旧配送",
            embedding=[0.0] * 1024,
        )
    )
    db_session.commit()
    (tmp_path / "配送时效.md").write_text("## 发货时间\n\n现货。\n", encoding="utf-8")

    count = ingest_documents(db_session, lambda text: [0.0] * 1024, tmp_path)

    rows = db_session.query(KnowledgeChunk).all()
    assert count == 1
    assert [row.document_name for row in rows] == ["配送时效.md"]
    assert rows[0].content == "## 发货时间\n现货。"


def test_return_question_uses_return_chunk_only(client, db_session):
    db_session.add(
        KnowledgeChunk(
            document_name="退货运费.md",
            content="七天无理由退货的运费由买家承担",
            embedding=RETURN_VECTOR,
        )
    )
    db_session.add(
        KnowledgeChunk(
            document_name="配送时效.md",
            content="现货订单支付后 48 小时内发货",
            embedding=SHIP_VECTOR,
        )
    )
    db_session.commit()

    seen: list[str] = []

    def fake_embed(text: str) -> list[float]:
        if "退货" in text or "运费" in text:
            return RETURN_VECTOR
        return SHIP_VECTOR

    app.dependency_overrides[get_complete] = lambda: _tool_model(
        seen, "search_knowledge", "退货运费由买家承担", {"query": "退货运费"}
    )
    app.dependency_overrides[get_embed] = lambda: fake_embed
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": conv.json()["conversation_id"], "message": "退货运费谁承担"},
    )
    assert res.status_code == 200
    followed = [item for item in seen if "买家承担" in item]
    assert followed
    assert all("48 小时" not in item for item in followed)


def test_order_slot_adds_order_status_to_prompt(client):
    seen: list[str] = []
    app.dependency_overrides[get_complete] = lambda: _tool_model(
        seen, "lookup_orders", "您的订单已发货"
    )
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={
            "conversation_id": conv.json()["conversation_id"],
            "message": "订单 ORD12345678 到哪了",
        },
    )
    assert res.status_code == 200
    assert any("已发货" in item for item in seen)


def test_my_orders_lists_without_amount_until_one_is_chosen(client):
    seen: list[str] = []
    app.dependency_overrides[get_complete] = lambda: _tool_model(seen, "lookup_orders", "请选择订单")
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    cid = conv.json()["conversation_id"]
    listed = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "我的订单到哪了"},
    )
    assert listed.status_code == 200
    listing = seen[-1]
    assert "ORD12345678" in listing
    assert "ORD12345679" in listing
    assert "299.00" not in listing
    assert "99.00" not in listing

    seen.clear()
    chosen = client.post(
        "/api/chat",
        headers=headers,
        json={"conversation_id": cid, "message": "键盘那单"},
    )
    assert chosen.status_code == 200
    detail = seen[-1]
    assert "299.00" in detail
    assert "已发货" in detail


def test_phone_and_name_match_keyboard_order(client):
    seen: list[str] = []
    app.dependency_overrides[get_complete] = lambda: _tool_model(
        seen, "lookup_orders", "键盘订单已发货"
    )
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={
            "conversation_id": conv.json()["conversation_id"],
            "message": "我叫张三，手机13800001111",
        },
    )
    assert res.status_code == 200
    detail = seen[-1]
    assert "ORD12345678" in detail
    assert "299.00" in detail
    assert "ORD12345679" not in detail


def test_other_persons_order_id_hides_amount(client):
    seen: list[str] = []
    app.dependency_overrides[get_complete] = lambda: _tool_model(seen, "lookup_orders", "未找到该订单")
    headers = {"Authorization": f"Bearer {_token(client, 'user1')}"}
    conv = client.post("/api/conversations", headers=headers)
    res = client.post(
        "/api/chat",
        headers=headers,
        json={
            "conversation_id": conv.json()["conversation_id"],
            "message": "帮我看 ORD87654321",
        },
    )
    assert res.status_code == 200
    detail = seen[-1]
    assert "199.00" not in detail
    assert "已签收" not in detail
    assert "未找到" in detail
