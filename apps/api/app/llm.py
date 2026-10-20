from collections.abc import Callable
from dataclasses import dataclass, field
import json
from typing import Annotated

import httpx
from fastapi import Depends

from app.config import Settings, get_settings


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ModelTurn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def complete(prompt: str, settings: Settings | None = None) -> str:
    """Call Ollama chat and return the assistant text. Raises LLMError on failure."""
    settings = settings or get_settings()
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    try:
        response = httpx.post(
            url,
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
    except Exception as exc:
        raise LLMError("LLM request failed") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM returned an empty reply")
    return content.strip()


def chat_with_tools(messages: list[dict], tools: list[dict], settings: Settings | None = None) -> ModelTurn:
    """Ask Ollama with tool schemas and return any requested tool calls."""
    settings = settings or get_settings()
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    try:
        response = httpx.post(
            url,
            json={
                "model": settings.llm_model,
                "messages": messages,
                "tools": tools,
                "stream": False,
                "think": False,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        message = response.json().get("message") or {}
    except Exception as exc:
        raise LLMError("LLM request failed") from exc
    content = message.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or raw
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments else {}
        calls.append(ToolCall(name=function.get("name") or "", arguments=arguments))
    return ModelTurn(content=content.strip(), tool_calls=calls)


def chat_with_tools_stream(
    messages: list[dict], tools: list[dict], settings: Settings | None = None
):
    """Yield Ollama content deltas and tool calls as they arrive."""
    settings = settings or get_settings()
    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
    try:
        with httpx.stream(
            "POST",
            url,
            json={
                "model": settings.llm_model,
                "messages": messages,
                "tools": tools,
                "stream": True,
                "think": False,
            },
            timeout=60.0,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                message = payload.get("message") or {}
                content = message.get("content") or ""
                if not isinstance(content, str):
                    content = str(content)
                calls: list[ToolCall] = []
                for raw in message.get("tool_calls") or []:
                    function = raw.get("function") or raw
                    arguments = function.get("arguments") or {}
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments) if arguments else {}
                        except json.JSONDecodeError:
                            arguments = {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                    calls.append(ToolCall(name=function.get("name") or "", arguments=arguments))
                if content or calls:
                    yield ModelTurn(content=content, tool_calls=calls)
    except Exception as exc:
        raise LLMError("LLM request failed") from exc


def embed(text: str, settings: Settings | None = None) -> list[float]:
    """Call Ollama embeddings. Raises LLMError on failure or a wrong dimension."""
    settings = settings or get_settings()
    url = f"{settings.ollama_base_url.rstrip('/')}/api/embed"
    try:
        response = httpx.post(
            url,
            json={"model": settings.embedding_model, "input": text},
            timeout=60.0,
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings")
        vector = embeddings[0] if embeddings else None
    except Exception as exc:
        raise LLMError("Embedding request failed") from exc
    if not isinstance(vector, list) or len(vector) != settings.embedding_dim:
        raise LLMError("Embedding dimension does not match configuration")
    return [float(value) for value in vector]


def get_embed(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Callable[[str], list[float]]:
    def _embed(text: str) -> list[float]:
        return embed(text, settings)

    return _embed


def get_complete(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Callable[[str], str]:
    def _complete(prompt: str) -> str:
        return complete(prompt, settings)

    def _with_tools(messages: list[dict], tools: list[dict]) -> ModelTurn:
        return chat_with_tools(messages, tools, settings)

    def _with_stream(messages: list[dict], tools: list[dict]):
        return chat_with_tools_stream(messages, tools, settings)

    _complete.with_tools = _with_tools
    _complete.with_stream = _with_stream
    return _complete
