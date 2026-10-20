import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.mcp_server import LookupOrdersResult, mcp, tool_request, ToolRequest


@contextmanager
def use_request_tools(
    db: Session,
    embed: Callable[[str], list[float]],
    user_id: str = "",
    message: str = "",
    slots: dict | None = None,
) -> Iterator[None]:
    token = tool_request.set(
        ToolRequest(
            db=db,
            embed=embed,
            user_id=user_id,
            message=message,
            slots=slots or {},
        )
    )
    try:
        yield
    finally:
        tool_request.reset(token)


def call_tool(name: str, arguments: dict):
    result = asyncio.run(mcp.call_tool(name, arguments))
    if result.is_error:
        raise RuntimeError(result.content[0].text if result.content else name)
    structured = result.structured_content or {}
    if "text" in structured and "candidates" in structured:
        return LookupOrdersResult.model_validate(structured)
    if "result" in structured:
        return structured["result"]
    if result.content:
        return result.content[0].text
    return None


def list_tool_names() -> list[str]:
    tools = asyncio.run(mcp.list_tools())
    return [tool.name for tool in tools]
