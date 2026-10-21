"""MCP 工具服务：同一份工具实现，两种暴露形态。

形态一（进程内）：聊天工具循环通过 mcp_client.call_tool 直调本模块注册的工具，
    身份/数据库走 contextvar（tool_request），零网络开销。
形态二（独立服务）：python -m app.mcp_server 起在 8001 端口，走标准 MCP 协议，
    任何外部 Agent 都能接；身份由 JwtContextMiddleware 从 Bearer token 解析，
    写入另一个 contextvar（mcp_http_user）。

安全核心（面试 ⭐⭐⭐）：lookup_orders 显式 del 掉调用方传入的 user_id/slots——
LLM 是不可信输入，身份永远来自服务端上下文，不来自模型参数。
"""

from contextvars import ContextVar
from collections.abc import Callable

import jwt
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.services.rag import search_knowledge as search_chunks
from app.services.tools import lookup_context, lookup_stock as find_stock

mcp = MCPServer("orl-tools")


class ToolRequest(BaseModel):
    """进程内调用时，工具循环注入的上下文包。db/embed 复用请求作用域，省一次建连。"""

    model_config = {"arbitrary_types_allowed": True}

    db: Session
    embed: Callable[[str], list[float]]
    user_id: str = ""
    message: str = ""  # 用户原话：订单号正则只从它提取，不信模型转述
    slots: dict = {}


# 两个 contextvar 对应两种形态：进程内身份 / HTTP 身份。互不干扰。
tool_request: ContextVar[ToolRequest | None] = ContextVar("tool_request", default=None)
mcp_http_user: ContextVar[str] = ContextVar("mcp_http_user", default="")


class LookupOrdersResult(BaseModel):
    """订单查询的结构化返回：text 给模型组织回答，order_id/candidates 给工单事件用。"""

    text: str
    order_id: str | None = None
    candidates: list[str] = []


@mcp.tool()
def search_knowledge(query: str) -> str:
    """知识库检索（RAG 取数侧）。独立形态下自建 db 会话并在 finally 关闭。"""
    current = tool_request.get()
    owned = False
    if current is None:
        # 独立 MCP 服务被外部调用：没有注入的 db，自己建一个短生命周期会话。
        from app.db import get_session_maker
        from app.llm import embed

        db = get_session_maker()()
        embed_fn = embed
        owned = True
    else:
        db = current.db
        embed_fn = current.embed
    try:
        chunks = search_chunks(db, embed_fn(query))
    except Exception:
        # 检索失败返回空串而不是抛错——让模型走「知识库暂无依据」的拒答路径。
        return ""
    finally:
        if owned:
            db.close()
    return "\n".join(chunks)


@mcp.tool()
def lookup_orders(user_id: str = "", message: str = "", slots: dict | None = None) -> LookupOrdersResult:
    """查询当前用户订单。归属安全的最关键一行就是下面的 del。

    del user_id, slots：显式丢弃调用方（可能是被注入的模型）传入的身份字段。
    即使提示注入成功诱导模型传入别人的 user_id，这行也让它失效——
    身份只认两个来源：进程内的 tool_request.user_id，或 HTTP 形态的 mcp_http_user。
    """
    del user_id, slots
    current = tool_request.get()
    if current is not None and current.user_id:
        # 形态一：进程内调用，身份来自 contextvar。
        try:
            text, order_id, candidates = lookup_context(
                current.db, current.slots or {}, current.user_id, current.message
            )
        except Exception:
            return LookupOrdersResult(text="未找到")
        return LookupOrdersResult(text=text, order_id=order_id, candidates=candidates)
    # 形态二：HTTP 调用，身份来自 JwtContextMiddleware 解析的 token。
    http_user = mcp_http_user.get()
    if not http_user:
        return LookupOrdersResult(text="未找到")
    from app.db import get_session_maker

    db = get_session_maker()()
    try:
        text, order_id, candidates = lookup_context(db, {}, http_user, message or "")
    except Exception:
        return LookupOrdersResult(text="未找到")
    finally:
        db.close()
    return LookupOrdersResult(text=text, order_id=order_id, candidates=candidates)


@mcp.tool()
def lookup_stock(query: str) -> str:
    """库存查询：公开数据，无需身份，但形态区分逻辑与 search_knowledge 相同。"""
    current = tool_request.get()
    owned = False
    if current is None:
        from app.db import get_session_maker

        db = get_session_maker()()
        owned = True
    else:
        db = current.db
    try:
        return find_stock(db, query)
    except Exception:
        return "未找到"
    finally:
        if owned:
            db.close()


def user_id_from_authorization(
    authorization: str | None, db: Session, settings: Settings
) -> str:
    """从 Authorization 头解析并校验 JWT，返回用户 id；任何一步失败都返回空串（匿名）。

    三层校验：签名有效 → payload 里有 sub → sub 对应的用户真实存在。
    """
    if authorization is None or not authorization.startswith("Bearer "):
        return ""
    token = authorization[7:].strip()
    if not token:
        return ""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return ""
    user_id = payload.get("sub")
    if not user_id:
        return ""
    from app.models import User

    user = db.get(User, user_id)
    if user is None:
        return ""
    return user.id


class JwtContextMiddleware:
    """ASGI 中间件：每个 HTTP 请求解析一次 Bearer token，把 user id 写入 contextvar。

    用 contextvar 而不是全局变量/请求参数，是因为 MCP 工具函数拿不到 request 对象，
    而 contextvar 在同一线程/协程上下文里天然隔离并发请求。
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers") or []
        }
        from app.config import get_settings
        from app.db import get_session_maker

        db = get_session_maker()()
        try:
            user_id = user_id_from_authorization(
                headers.get("authorization"), db, get_settings()
            )
        finally:
            db.close()
        token = mcp_http_user.set(user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            # finally 里 reset：请求结束（无论成败）都清空身份，防止串到下一个请求。
            mcp_http_user.reset(token)


def main() -> None:
    import uvicorn

    app = JwtContextMiddleware(mcp.streamable_http_app())
    config = uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="info")
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
