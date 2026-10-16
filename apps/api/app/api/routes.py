from collections.abc import Callable
from datetime import datetime, timezone, timedelta
from typing import Annotated, Optional

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.domain.tickets import InvalidTransition, transition
from app.models import (
    Conversation,
    ConversationSlot,
    Message,
    Ticket,
    TicketEvent,
    User,
    password_hash,
)
from app.services.rewrite import extract_slots, merge_slots, rewrite_query

router = APIRouter(prefix="/api")


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    role: str
    user_id: str


class ConversationResponse(BaseModel):
    conversation_id: str


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


class ChatResponse(BaseModel):
    ticket_id: Optional[str]
    mode: str
    rewritten_query: Optional[str]


class ClaimResponse(BaseModel):
    status: str


def create_token(user: User, settings: Settings) -> str:
    payload = {
        "sub": user.id,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(
        payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )


def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[Optional[str], Header()] = None,
) -> User:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("/auth/login", response_model=LoginResponse)
def login(
    req: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginResponse:
    user = db.query(User).filter(User.username == req.username).first()
    if user is None or not password_hash.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return LoginResponse(
        access_token=create_token(user, settings),
        role=user.role,
        user_id=user.id,
    )


@router.post("/conversations", response_model=ConversationResponse)
def create_conversation(user: CurrentUser, db: Annotated[Session, Depends(get_db)]) -> ConversationResponse:
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Only customers can create conversations")
    conversation = Conversation(customer_id=user.id)
    db.add(conversation)
    db.flush()
    db.add(ConversationSlot(conversation_id=conversation.id))
    db.commit()
    return ConversationResponse(conversation_id=conversation.id)


@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ChatResponse:
    conversation = db.get(Conversation, req.conversation_id)
    if conversation is None or conversation.customer_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    def update_slots(found: dict[str, str]) -> dict[str, str | None]:
        slots_row = db.get(ConversationSlot, conversation.id)
        merged = merge_slots(
            {
                "last_order_id": slots_row.last_order_id,
                "last_product_id": slots_row.last_product_id,
            }
            if slots_row
            else {},
            found,
        )
        if slots_row is None:
            slots_row = ConversationSlot(conversation_id=conversation.id)
            db.add(slots_row)
        slots_row.last_order_id = merged.get("last_order_id")
        slots_row.last_product_id = merged.get("last_product_id")
        return merged

    if conversation.mode == "human":
        db.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content=req.message,
            )
        )
        found = extract_slots(req.message)
        if found:
            update_slots(found)
        ticket = (
            db.query(Ticket)
            .filter(Ticket.conversation_id == conversation.id)
            .filter(Ticket.status.in_(["open", "in_progress"]))
            .first()
        )
        db.commit()
        return ChatResponse(
            ticket_id=ticket.id if ticket else None,
            mode="human",
            rewritten_query=None,
        )

    if "转人工" in req.message or "人工客服" in req.message:
        db.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content=req.message,
            )
        )
        found = extract_slots(req.message)
        if found:
            update_slots(found)
        ticket = Ticket(
            conversation_id=conversation.id,
            status="open",
        )
        db.add(ticket)
        db.flush()
        db.add(
            TicketEvent(
                ticket_id=ticket.id,
                type="created",
                to_status="open",
            )
        )
        conversation.mode = "human"
        db.add(
            Message(
                conversation_id=conversation.id,
                role="system",
                content="已转接人工客服",
            )
        )
        db.commit()
        return ChatResponse(ticket_id=ticket.id, mode="human", rewritten_query=None)

    history = [
        {"role": m.role, "content": m.content}
        for m in db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
        .all()
    ]

    found = extract_slots(req.message)
    merged = update_slots(found)

    complete: Callable[[str], str] = lambda _prompt: req.message
    rewritten = rewrite_query(req.message, history, merged, complete)

    db.add(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=req.message,
        )
    )
    db.commit()
    return ChatResponse(ticket_id=None, mode="bot", rewritten_query=rewritten)


@router.post("/tickets/{ticket_id}/claim", response_model=ClaimResponse)
def claim_ticket(
    ticket_id: str,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ClaimResponse:
    if user.role != "agent":
        raise HTTPException(status_code=403, detail="Only agents can claim tickets")
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status != "open":
        raise HTTPException(status_code=409, detail="Ticket is not open")
    try:
        new_status = transition(ticket.status, "claim")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail="Ticket cannot be claimed") from exc
    old_status = ticket.status
    ticket.status = new_status
    ticket.assignee_id = user.id
    db.add(
        TicketEvent(
            ticket_id=ticket.id,
            actor_id=user.id,
            type="claim",
            from_status=old_status,
            to_status=new_status,
        )
    )
    db.commit()
    return ClaimResponse(status=new_status)
