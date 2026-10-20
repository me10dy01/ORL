import httpx
import pytest

from app.config import Settings
from app.llm import LLMError, chat_with_tools, chat_with_tools_stream, complete, embed


def test_complete_reads_ollama_chat(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": " 你好 "}}

    def fake_post(url, json, timeout):
        assert url == "http://127.0.0.1:11434/api/chat"
        assert json["model"] == "qwen3.5:4b"
        assert json["stream"] is False
        assert json["think"] is False
        assert json["messages"] == [{"role": "user", "content": "hi"}]
        assert timeout == 60.0
        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    assert complete("hi", Settings()) == "你好"


def test_complete_rejects_empty_content(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "  "}}

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: Response())
    with pytest.raises(LLMError):
        complete("hi", Settings())


def test_tool_chat_disables_thinking(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": "好", "tool_calls": []}}

    def fake_post(url, json, timeout):
        seen.update(json)
        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    chat_with_tools([{"role": "user", "content": "hi"}], [], Settings())
    assert seen["think"] is False


def test_tool_stream_disables_thinking(monkeypatch):
    seen = {}

    class Response:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield '{"message": {"content": "好"}}'

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

    def fake_stream(method, url, json, timeout):
        seen.update(json)
        return Response()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    turns = list(chat_with_tools_stream([{"role": "user", "content": "hi"}], [], Settings()))
    assert seen["think"] is False
    assert turns[0].content == "好"


def test_complete_raises_when_ollama_is_down(monkeypatch):
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(LLMError):
        complete("hi", Settings())


def test_embed_reads_ollama_embeddings(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embeddings": [[0.5] * 1024]}

    def fake_post(url, json, timeout):
        assert url == "http://127.0.0.1:11434/api/embed"
        assert json["model"] == "qwen3-embedding:0.6b"
        assert json["input"] == "退货运费"
        return Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    assert len(embed("退货运费", Settings())) == 1024


def test_embed_rejects_wrong_dimension(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embeddings": [[0.1, 0.2]]}

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: Response())
    with pytest.raises(LLMError):
        embed("退货运费", Settings())
