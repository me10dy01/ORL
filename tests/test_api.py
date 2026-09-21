"""Phase 13 验收测试：FastAPI 可独立调用完整 Workflow。"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


# ---- 快速测试（不调用 LLM）----


@pytest.mark.usefixtures("initialized_db")
def test_list_documents() -> None:
    resp = client.get("/api/documents")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.usefixtures("initialized_db")
def test_list_conversations() -> None:
    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.usefixtures("initialized_db")
def test_conversation_rename_and_delete() -> None:
    from app import memory

    cid = f"api-conv-{uuid.uuid4().hex[:8]}"
    memory.add_message(cid, "user", "你好")

    # 重命名
    resp = client.patch(f"/api/conversations/{cid}", json={"title": "测试会话"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "测试会话"

    listed = {c["conversation_id"]: c for c in client.get("/api/conversations").json()}
    assert listed[cid]["title"] == "测试会话"

    # 删除
    resp = client.delete(f"/api/conversations/{cid}")
    assert resp.status_code == 200
    assert resp.json()["deleted_messages"] == 1

    # 再删 → 404
    assert client.delete(f"/api/conversations/{cid}").status_code == 404


@pytest.mark.usefixtures("initialized_db")
def test_conversation_rename_missing_returns_404() -> None:
    resp = client.patch("/api/conversations/api-conv-not-exist", json={"title": "x"})
    assert resp.status_code == 404


@pytest.mark.usefixtures("initialized_db")
def test_cache_endpoints() -> None:
    resp = client.get("/api/cache")
    assert resp.status_code == 200
    body = resp.json()
    assert "entries" in body and "hits" in body
    assert isinstance(body["hits"], int)

    resp = client.delete("/api/cache")
    assert resp.status_code == 200
    assert "cleared" in resp.json()


def test_faq_endpoint() -> None:
    """常见问题列表：静态数据，无需 DB / LLM，且不针对某一件商品。"""
    from app.faq import load_faq

    load_faq.cache_clear()
    resp = client.get("/api/faq")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list) and items
    questions = [item.get("question", "") for item in items]
    assert all(questions)
    blob = " ".join(questions)
    assert "H50" not in blob
    assert "K87" not in blob
    assert "怎么查看商品的规格和参数？" in questions
    assert "怎么查询商品价格和库存？" in questions


@pytest.mark.usefixtures("initialized_db")
def test_conversation_batch_delete() -> None:
    """列表“全选删除”：批量删除多个会话及其消息。"""
    from app import memory

    ids = [f"api-batch-{uuid.uuid4().hex[:8]}" for _ in range(3)]
    for cid in ids:
        memory.add_message(cid, "user", "hi")

    resp = client.post(
        "/api/conversations/batch-delete", json={"conversation_ids": ids}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted_conversations"] == 3
    assert body["deleted_messages"] == 3

    remaining = {c["conversation_id"] for c in client.get("/api/conversations").json()}
    assert not (set(ids) & remaining)


def test_conversation_batch_delete_empty_rejected() -> None:
    resp = client.post("/api/conversations/batch-delete", json={"conversation_ids": []})
    assert resp.status_code == 422


def test_conversation_rename_empty_title_rejected() -> None:
    resp = client.patch("/api/conversations/whatever", json={"title": ""})
    assert resp.status_code == 422


def test_chat_request_validation() -> None:
    resp = client.post("/api/chat", json={"message": ""})
    assert resp.status_code == 422


# ---- 慢测试（LLM / Embedding）----


@pytest.mark.slow
@pytest.mark.usefixtures("initialized_db", "rag_index")
def test_chat_api_runs_full_workflow() -> None:
    cid = f"api-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/chat",
        json={"message": "商城退货政策是什么？", "conversation_id": cid},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == cid
    assert body["route"] == "rag"
    assert body["final_answer"]
    assert body["citations"]


@pytest.mark.slow
@pytest.mark.usefixtures("initialized_db", "rag_index")
def test_chat_stream_api_emits_events() -> None:
    cid = f"api-{uuid.uuid4().hex[:8]}"
    events: list[dict] = []
    with client.stream(
        "POST",
        "/api/chat/stream",
        json={"message": "商城退货政策是什么？", "conversation_id": cid},
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    types = {e["type"] for e in events}
    assert "meta" in types
    assert "status" in types
    assert "done" in types
    done = [e for e in events if e["type"] == "done"][0]
    assert done["final_answer"]


@pytest.mark.slow
@pytest.mark.usefixtures("rag_index")
def test_upload_document() -> None:
    from app.rag import management
    from app.seed import DOCUMENTS_DIR

    name = f"__test_{uuid.uuid4().hex[:6]}.txt"
    content = "临时测试文档。这是用于验证多格式上传接口的内容。".encode()
    try:
        resp = client.post(
            "/api/documents",
            files={"file": (name, content, "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["file_name"] == name
        assert body["ingested_chunks"] >= 1
    finally:
        docs = {d["file_name"]: d["document_id"] for d in management.list_documents()}
        if name in docs:
            management.delete_document(docs[name])
        (DOCUMENTS_DIR / name).unlink(missing_ok=True)


@pytest.mark.usefixtures("initialized_db")
def test_upload_rejects_unsupported_format() -> None:
    resp = client.post(
        "/api/documents",
        files={"file": ("bad.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")},
    )
    assert resp.status_code == 400


# ---- 文档删除 / 修改（管理页按钮）----


@pytest.mark.usefixtures("initialized_db")
def test_delete_document_missing_returns_404() -> None:
    resp = client.delete("/api/documents/doc_not_exist")
    assert resp.status_code == 404


@pytest.mark.usefixtures("initialized_db")
def test_update_document_missing_returns_404() -> None:
    resp = client.put(
        "/api/documents/doc_not_exist",
        files={"file": ("whatever.txt", "内容".encode(), "text/plain")},
    )
    assert resp.status_code == 404


@pytest.mark.usefixtures("initialized_db")
def test_update_document_rejects_unsupported_format() -> None:
    resp = client.put(
        "/api/documents/doc_any",
        files={"file": ("bad.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")},
    )
    assert resp.status_code == 400


@pytest.mark.slow
@pytest.mark.usefixtures("initialized_db", "rag_index")
def test_delete_document_flow() -> None:
    """上传 → 删除：向量与源文件均被清理，重复删除返回 404。"""
    from app.rag import management
    from app.seed import DOCUMENTS_DIR

    name = f"__test_del_{uuid.uuid4().hex[:6]}.txt"
    try:
        resp = client.post(
            "/api/documents",
            files={"file": (name, "删除测试文档内容。".encode(), "text/plain")},
        )
        assert resp.status_code == 200
        doc_id = _doc_id_by_name(name)

        resp = client.delete(f"/api/documents/{doc_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted_chunks"] >= 1
        assert body["file_removed"] is True
        assert name not in {d["file_name"] for d in client.get("/api/documents").json()}
        assert not (DOCUMENTS_DIR / name).exists()

        # 再删 → 404
        assert client.delete(f"/api/documents/{doc_id}").status_code == 404
    finally:
        (DOCUMENTS_DIR / name).unlink(missing_ok=True)


@pytest.mark.slow
@pytest.mark.usefixtures("initialized_db", "rag_index")
def test_update_document_same_name_replaces_chunks() -> None:
    """同名修改：document_id 不变，旧版本 chunk 被替换而非混存。"""
    from app.rag import documents as rag_documents
    from app.rag import management
    from app.seed import DOCUMENTS_DIR

    name = f"__test_upd_{uuid.uuid4().hex[:6]}.txt"
    content_v1 = "以旧换新服务最高抵扣 500 元。".encode()
    content_v2 = "以旧换新服务最高抵扣 800 元，环保补贴 60 元。".encode()
    try:
        resp = client.post(
            "/api/documents", files={"file": (name, content_v1, "text/plain")}
        )
        assert resp.status_code == 200
        doc_id = _doc_id_by_name(name)
        old_hash = rag_documents._md5(content_v1.decode())

        resp = client.put(
            f"/api/documents/{doc_id}", files={"file": (name, content_v2, "text/plain")}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["changed"] is True
        assert body["old_document_id"] == doc_id
        assert body["new_document_id"] == doc_id  # 同名：id 稳定
        assert body["deleted_chunks"] >= 1
        assert body["ingested_chunks"] >= 1

        # 向量库中该文档只剩新版本的 file_hash
        entry = {d["file_name"]: d for d in client.get("/api/documents").json()}[name]
        assert entry["document_id"] == doc_id
        assert entry["file_hash"] == rag_documents._md5(content_v2.decode())
        assert entry["file_hash"] != old_hash
    finally:
        docs = {d["file_name"]: d["document_id"] for d in management.list_documents()}
        if name in docs:
            management.delete_document(docs[name])
        (DOCUMENTS_DIR / name).unlink(missing_ok=True)


@pytest.mark.slow
@pytest.mark.usefixtures("initialized_db", "rag_index")
def test_update_document_same_content_is_idempotent() -> None:
    """内容未变化（hash 一致）：changed=False，不做任何写操作。"""
    from app.rag import management
    from app.seed import DOCUMENTS_DIR

    name = f"__test_idem_{uuid.uuid4().hex[:6]}.txt"
    content = "幂等测试文档内容。".encode()
    try:
        client.post("/api/documents", files={"file": (name, content, "text/plain")})
        doc_id = _doc_id_by_name(name)
        before = {
            d["file_name"]: d["chunk_count"] for d in client.get("/api/documents").json()
        }

        resp = client.put(
            f"/api/documents/{doc_id}", files={"file": (name, content, "text/plain")}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["changed"] is False
        assert body["ingested_chunks"] == 0
        after = {
            d["file_name"]: d["chunk_count"] for d in client.get("/api/documents").json()
        }
        assert after == before  # chunk 数不变
    finally:
        docs = {d["file_name"]: d["document_id"] for d in management.list_documents()}
        if name in docs:
            management.delete_document(docs[name])
        (DOCUMENTS_DIR / name).unlink(missing_ok=True)


@pytest.mark.slow
@pytest.mark.usefixtures("initialized_db", "rag_index")
def test_update_document_with_new_name_replaces_old() -> None:
    """换名修改：入库新文档并删除旧文档（向量 + 源文件）。"""
    from app.rag import management
    from app.seed import DOCUMENTS_DIR

    old_name = f"__test_ren_old_{uuid.uuid4().hex[:6]}.txt"
    new_name = f"__test_ren_new_{uuid.uuid4().hex[:6]}.txt"
    try:
        client.post(
            "/api/documents", files={"file": (old_name, "旧版本内容。".encode(), "text/plain")}
        )
        old_id = _doc_id_by_name(old_name)

        resp = client.put(
            f"/api/documents/{old_id}",
            files={"file": (new_name, "新版本内容，名称不同。".encode(), "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["changed"] is True
        assert body["new_document_id"] != old_id
        assert body["deleted_chunks"] >= 1

        files_now = {d["file_name"] for d in client.get("/api/documents").json()}
        assert new_name in files_now
        assert old_name not in files_now
        assert not (DOCUMENTS_DIR / old_name).exists()
    finally:
        for n in (old_name, new_name):
            docs = {d["file_name"]: d["document_id"] for d in management.list_documents()}
            if n in docs:
                management.delete_document(docs[n])
            (DOCUMENTS_DIR / n).unlink(missing_ok=True)


def _doc_id_by_name(name: str) -> str:
    from app.rag import management

    for d in management.list_documents():
        if d["file_name"] == name:
            return d["document_id"]
    raise AssertionError(f"文档未入库: {name}")
