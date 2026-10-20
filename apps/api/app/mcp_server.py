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
    model_config = {"arbitrary_types_allowed": True}

    db: Session
    embed: Callable[[str], list[float]]
    user_id: str = ""
    message: str = ""
    slots: dict = {}


tool_request: ContextVar[ToolRequest | None] = ContextVar("tool_request", default=None)
mcp_http_user: ContextVar[str] = ContextVar("mcp_http_user", default="")


class LookupOrdersResult(BaseModel):
    text: str
    order_id: str | None = None
    candidates: list[str] = []


@mcp.tool()
def search_knowledge(query: str) -> str:
    """Search the knowledge base and return matching passages."""
    current = tool_request.get()
    owned = False
    if current is None:
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
        return ""
    finally:
        if owned:
            db.close()
    return "\n".join(chunks)


@mcp.tool()
def lookup_orders(user_id: str = "", message: str = "", slots: dict | None = None) -> LookupOrdersResult:
    """Look up orders for the signed-in user. Caller-supplied user ids and slots are ignored."""
    del user_id, slots
    current = tool_request.get()
    if current is not None and current.user_id:
        try:
            text, order_id, candidates = lookup_context(
                current.db, current.slots or {}, current.user_id, current.message
            )
        except Exception:
            return LookupOrdersResult(text="未找到")
        return LookupOrdersResult(text=text, order_id=order_id, candidates=candidates)
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
    """Look up public product stock by name or product id."""
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
    """Return the user id for a login JWT. Invalid tokens return an empty string."""
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
    """Store the Bearer user for this HTTP request. Missing tokens stay anonymous."""

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
            mcp_http_user.reset(token)


def main() -> None:
    import uvicorn

    app = JwtContextMiddleware(mcp.streamable_http_app())
    config = uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="info")
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
