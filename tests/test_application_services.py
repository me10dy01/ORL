"""应用服务层边界测试：HTTP 路由之外的用例可独立调用。"""

from __future__ import annotations

import importlib

import pytest

from app.application import (
    chat_service,
    conversation_service,
    knowledge_service,
    suggestion_service,
)
from app.exceptions import PayloadTooLargeError, ValidationError

rag_agent_module = importlib.import_module("app.agents.rag_agent")


def test_chat_service_delegates_to_workflow(monkeypatch) -> None:
    expected = {"route": "rag", "final_answer": "回答"}
    monkeypatch.setattr(chat_service, "run_main_graph", lambda message, conversation_id: expected)

    cid, result = chat_service.run_chat("问题", "conv-test")

    assert cid == "conv-test"
    assert result is expected


def test_stream_service_owns_conversation_id(monkeypatch) -> None:
    events = iter([{"type": "done"}])
    monkeypatch.setattr(chat_service, "stream_chat", lambda message, conversation_id: events)

    cid, actual = chat_service.stream_chat_events("问题", "conv-stream")

    assert cid == "conv-stream"
    assert list(actual) == [{"type": "done"}]


def test_conversation_service_shapes_detail(monkeypatch) -> None:
    monkeypatch.setattr(
        conversation_service.memory,
        "get_recent_messages",
        lambda conversation_id, limit: [{"role": "user", "content": "你好"}],
    )

    assert conversation_service.get_conversation("conv-1") == {
        "conversation_id": "conv-1",
        "messages": [{"role": "user", "content": "你好"}],
    }


def test_suggestion_service_returns_empty_without_history(monkeypatch) -> None:
    monkeypatch.setattr(
        suggestion_service.memory, "get_recent_messages", lambda *_, **__: []
    )
    assert suggestion_service.suggest_questions("conv-empty") == []


def test_suggestion_disables_reasoning_and_keeps_questions(monkeypatch) -> None:
    captured: dict = {}

    class _Response:
        content = "<think>先想一下</think>\n1. 订单号在哪里看？\n发货后一般几天能到？"

    def fake_get_chat_model(**kwargs):
        captured.update(kwargs)

        class _LLM:
            def invoke(self, messages):
                return _Response()

        return _LLM()

    monkeypatch.setattr(suggestion_service, "get_chat_model", fake_get_chat_model)
    questions = suggestion_service.suggest_questions("conv-1", context_text="用户: 怎么查订单")
    assert captured.get("reasoning") is False
    assert questions == ["订单号在哪里看？", "发货后一般几天能到？"]


def test_rename_rejects_blank_title() -> None:
    with pytest.raises(ValidationError):
        conversation_service.rename_conversation("conv-1", "   ")


def test_ingest_rejects_type_and_oversize(monkeypatch) -> None:
    monkeypatch.setattr(knowledge_service, "MAX_UPLOAD_BYTES", 4)
    with pytest.raises(ValidationError):
        knowledge_service.ingest_document("notes.xlsx", b"hi")
    with pytest.raises(PayloadTooLargeError):
        knowledge_service.ingest_document("notes.txt", b"12345")


def test_rag_agent_uses_pipeline_boundary(monkeypatch) -> None:
    docs = [{"content": "依据", "metadata": {"chunk_id": "c1"}}]
    monkeypatch.setattr(
        rag_agent_module.pipeline, "retrieve_for_answer", lambda query: docs
    )

    assert rag_agent_module.retrieve_node({"query": "问题"}) == {"retrieved_docs": docs}


def test_stream_presenter_hides_internal_result() -> None:
    from app.streaming import _present

    events = list(
        _present(
            iter(
                [
                    {"type": "status", "message": "正在分析问题"},
                    {
                        "type": "done",
                        "final_answer": "ab",
                        "citations": [],
                        "result": {"route": "rag"},
                    },
                ]
            )
        )
    )
    assert events[0] == {"type": "status", "message": "正在分析问题"}
    assert {"type": "token", "content": "ab"} in events
    done = events[-1]
    assert done["final_answer"] == "ab"
    assert "result" not in done
