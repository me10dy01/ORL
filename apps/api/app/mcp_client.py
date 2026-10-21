"""进程内 MCP 调用适配层：让工具循环不用走网络就能调用 mcp_server 注册的工具。

为什么要有这层：MCP 标准接口是异步的（asyncio），而 FastAPI 路由是同步风格；
call_tool 用 asyncio.run 桥接，并把 MCP 的 Result 包装解包成业务返回值。
"""

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
    """上下文管理器：进入时把请求上下文写入 contextvar，退出时（finally）复位。

    面试考点：为什么用 contextvar 传身份而不是函数参数？
    ——工具函数签名是给模型看的（JSON Schema），身份字段一旦出现在签名里就有被注入的风险；
    contextvar 让身份「穿过」调用栈到达工具内部，却对模型完全不可见。
    """
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
    """同步调用 MCP 工具并解包结果。

    解包优先级：结构化内容（LookupOrdersResult）> result 标量 > 文本内容。
    is_error 时抛 RuntimeError，让调用方（tool_loop._execute）决定降级策略。
    """
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
    """列出已注册的工具名（调试用，也用于测试断言三个工具都已注册）。"""
    tools = asyncio.run(mcp.list_tools())
    return [tool.name for tool in tools]
