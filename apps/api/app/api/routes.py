from collections.abc import Callable
from datetime import datetime, timezone, timedelta
import json
from typing import Annotated, Optional

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.domain.tickets import InvalidTransition, claim_if_open, transition
from app.llm import get_complete, get_embed
from app.models import (
    Conversation,
    ConversationSlot,
    Message,
    Ticket,
    TicketEvent,
    User,
    UserProfile,
    password_hash,
)
from app.services.bot import ANSWER_FALLBACK
from app.services.redis_bus import allow_chat, listen, publish
from app.services.graph import (
    build_chat_graph,
    fill_ticket,
    lookup_payload,
    needs_refusal_handoff,
    triage_update,
)
from app.services.profile import get_profile, remember_profile
from app.services.rewrite import extract_slots, merge_slots, rewrite_query
from app.services.tool_loop import iter_answer_events

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


class ConversationListItem(BaseModel):
    conversation_id: str
    mode: str
    preview: str = ""


class ConversationListResponse(BaseModel):
    conversations: list[ConversationListItem]


class ConversationMessage(BaseModel):
    role: str
    content: str
    created_at: datetime


class ConversationDetailResponse(BaseModel):
    conversation_id: str
    mode: str
    messages: list[ConversationMessage]


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


class ChatResponse(BaseModel):
    ticket_id: Optional[str]
    mode: str
    rewritten_query: Optional[str]
    assistant_message: Optional[str] = None


class ClaimResponse(BaseModel):
    status: str


class TicketInfo(BaseModel):
    id: str
    conversation_id: str
    status: str
    priority: str
    category: str
    summary: str
    assignee_id: Optional[str]
    created_at: datetime


class TicketListResponse(BaseModel):
    tickets: list[TicketInfo]


class MessageInfo(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime


class EventInfo(BaseModel):
    id: str
    type: str
    actor_id: Optional[str]
    from_status: Optional[str]
    to_status: Optional[str]


class ProfileInfo(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    last_product_id: Optional[str] = None
    last_ticket_summary: Optional[str] = None


class TicketDetailResponse(BaseModel):
    ticket: TicketInfo
    messages: list[MessageInfo]
    events: list[EventInfo]
    profile: Optional[ProfileInfo] = None


class AgentMessageRequest(BaseModel):
    content: str


class AgentMessageResponse(BaseModel):
    message_id: str


class TransitionRequest(BaseModel):
    action: str


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
Completer = Annotated[Callable[[str], str], Depends(get_complete)]
Embedder = Annotated[Callable[[str], list[float]], Depends(get_embed)]


def _require_agent(user: User) -> None:
    if user.role != "agent":
        raise HTTPException(status_code=403, detail="Only agents can access tickets")


def _ticket_info(ticket: Ticket) -> TicketInfo:
    return TicketInfo(
        id=ticket.id,
        conversation_id=ticket.conversation_id,
        status=ticket.status,
        priority=ticket.priority,
        category=ticket.category,
        summary=ticket.summary,
        assignee_id=ticket.assignee_id,
        created_at=ticket.created_at,
    )


def _open_ticket(
    db: Session,
    conversation: Conversation,
    priority: str,
    category: str,
    summary: str,
) -> Ticket:
    ticket = Ticket(
        conversation_id=conversation.id,
        status="open",
        priority=priority,
        category=category,
        summary=summary,
    )
    db.add(ticket)
    db.flush()
    db.add(TicketEvent(ticket_id=ticket.id, type="created", to_status="open"))
    conversation.mode = "human"
    db.add(
        Message(
            conversation_id=conversation.id,
            role="system",
            content="已转接人工客服",
        )
    )
    publish("refresh")
    return ticket


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


@router.get("/conversations/me", response_model=ConversationListResponse)
def list_my_conversations(
    user: CurrentUser, db: Annotated[Session, Depends(get_db)]
) -> ConversationListResponse:
    if user.role != "customer":
        raise HTTPException(status_code=403, detail="Only customers can list conversations")
    rows = db.query(Conversation).filter(Conversation.customer_id == user.id).all()

    def latest(conversation: Conversation) -> datetime:
        stamp = (
            db.query(Message.created_at)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .first()
        )
        if not stamp or stamp[0] is None:
            return datetime.min
        value = stamp[0]
        if value.tzinfo is not None:
            return value.replace(tzinfo=None)
        return value

    rows.sort(key=latest, reverse=True)

    def preview_for(conversation: Conversation) -> str:
        row = (
            db.query(Message.content)
            .filter(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .first()
        )
        if not row or not row[0]:
            return ""
        text = row[0].strip()
        if len(text) <= 40:
            return text
        return text[:40] + "…"

    return ConversationListResponse(
        conversations=[
            ConversationListItem(
                conversation_id=row.id,
                mode=row.mode,
                preview=preview_for(row),
            )
            for row in rows
        ]
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(
    conversation_id: str,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ConversationDetailResponse:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.customer_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
        .all()
    )
    return ConversationDetailResponse(
        conversation_id=conversation.id,
        mode=conversation.mode,
        messages=[
            ConversationMessage(role=item.role, content=item.content, created_at=item.created_at)
            for item in messages
        ],
    )


class _BotContext:
    def __init__(self, conversation: Conversation, recent: list[dict], slots: dict, rewritten: str):
        self.conversation = conversation
        self.recent = recent
        self.slots = slots
        self.rewritten = rewritten


def _remember_lookup(db: Session, conversation: Conversation, result) -> None:
    if not result:
        return
    if isinstance(result, dict):
        order_id = result.get("order_id")
        candidates = result.get("candidates") or []
        if "order_id" not in result and "candidates" not in result:
            return
    else:
        order_id = result.order_id
        candidates = result.candidates or []
    slots_row = db.get(ConversationSlot, conversation.id)
    if slots_row is None:
        slots_row = ConversationSlot(conversation_id=conversation.id)
        db.add(slots_row)
    slots_row.last_order_id = order_id
    slots_row.candidate_order_ids = ",".join(candidates) if candidates else None


def _persist_bot_reply(
    db: Session,
    conversation: Conversation,
    req: ChatRequest,
    rewritten: str,
    reply: str,
    lookup_result,
    *,
    commit: bool = True,
) -> ChatResponse:
    reply = (reply or "").strip() or ANSWER_FALLBACK
    _remember_lookup(db, conversation, lookup_result)
    db.add(Message(conversation_id=conversation.id, role="user", content=req.message))
    db.add(Message(conversation_id=conversation.id, role="assistant", content=reply))
    if commit:
        db.commit()
    return ChatResponse(
        ticket_id=None,
        mode="bot",
        rewritten_query=rewritten,
        assistant_message=reply,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    complete: Completer,
    embed: Embedder,
) -> ChatResponse:
    conversation = db.get(Conversation, req.conversation_id)
    if conversation is None or conversation.customer_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not allow_chat(user.id):
        raise HTTPException(status_code=429, detail="消息太频繁，请稍后再发")

    def update_slots(found: dict[str, str]) -> dict[str, str | None]:
        slots_row = db.get(ConversationSlot, conversation.id)
        current = (
            {
                "last_order_id": slots_row.last_order_id,
                "last_product_id": slots_row.last_product_id,
                "last_phone": slots_row.last_phone,
                "last_name": slots_row.last_name,
                "candidate_order_ids": slots_row.candidate_order_ids,
            }
            if slots_row
            else {}
        )
        merged = merge_slots(current, found)
        merged["candidate_order_ids"] = current.get("candidate_order_ids")
        if slots_row is None:
            slots_row = ConversationSlot(conversation_id=conversation.id)
            db.add(slots_row)
        slots_row.last_product_id = merged.get("last_product_id")
        slots_row.last_phone = merged.get("last_phone")
        slots_row.last_name = merged.get("last_name")
        remember_profile(
            db,
            user.id,
            name=merged.get("last_name"),
            phone=merged.get("last_phone"),
            last_product_id=merged.get("last_product_id"),
        )
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
            assistant_message=None,
        )

    decision = triage_update(req.message)
    history = [
        {"role": m.role, "content": m.content}
        for m in db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
        .all()
    ]
    recent = history[-10:]
    found = extract_slots(req.message)
    merged = update_slots(found)
    if not decision.get("handoff"):
        try:
            rewritten = rewrite_query(req.message, recent, merged, complete)
        except Exception:
            rewritten = req.message
    else:
        rewritten = req.message

    graph = build_chat_graph(complete, db=db, embed=embed)
    outcome = graph.invoke(
        {
            "message": req.message,
            "history": recent,
            "rewritten": rewritten,
            "slots": merged,
            "user_id": user.id,
        }
    )
    if outcome.get("handoff") and not outcome.get("reply"):
        db.add(
            Message(
                conversation_id=conversation.id,
                role="user",
                content=req.message,
            )
        )
        ticket = _open_ticket(
            db,
            conversation,
            outcome["priority"],
            outcome["category"],
            outcome["summary"],
        )
        db.commit()
        return ChatResponse(
            ticket_id=ticket.id,
            mode="human",
            rewritten_query=None,
            assistant_message=None,
        )
    if outcome.get("handoff"):
        saved = _persist_bot_reply(
            db,
            conversation,
            req,
            rewritten,
            outcome.get("reply") or "",
            outcome.get("lookup") or {},
            commit=False,
        )
        ticket = _open_ticket(
            db,
            conversation,
            outcome.get("priority") or "normal",
            outcome["category"],
            outcome["summary"],
        )
        db.commit()
        return ChatResponse(
            ticket_id=ticket.id,
            mode="human",
            rewritten_query=None,
            assistant_message=saved.assistant_message,
        )
    return _persist_bot_reply(
        db,
        conversation,
        req,
        rewritten,
        outcome.get("reply") or "",
        outcome.get("lookup") or {},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _finished_events(db: Session, req: ChatRequest, result: ChatResponse):
    yield _sse({"type": "meta", "conversation_id": req.conversation_id})
    if result.ticket_id:
        yield _sse({"type": "status", "message": "正在转人工"})
        ticket = db.get(Ticket, result.ticket_id)
        payload = {
            "type": "ticket_created",
            "ticket_id": result.ticket_id,
            "mode": result.mode,
        }
        if ticket is not None:
            payload["priority"] = ticket.priority
        yield _sse(payload)
        final_answer = result.assistant_message or "已转接人工客服"
    else:
        final_answer = result.assistant_message or ""
        if final_answer:
            yield _sse({"type": "token", "content": final_answer})
    yield _sse(
        {
            "type": "done",
            "final_answer": final_answer,
            "ticket_id": result.ticket_id,
            "mode": result.mode,
            "rewritten_query": result.rewritten_query,
        }
    )


def _bot_events(
    req: ChatRequest,
    user: User,
    db: Session,
    complete,
    embed,
    conversation: Conversation,
    recent: list[dict],
    merged: dict,
    rewritten: str,
):
    yield _sse({"type": "meta", "conversation_id": req.conversation_id})
    lookup_box: dict = {"result": None}
    parts: list[str] = []
    try:
        for event in iter_answer_events(
            complete,
            db=db,
            embed=embed,
            user_id=user.id,
            message=req.message,
            slots=merged,
            rewritten=rewritten,
            history=recent,
            lookup_box=lookup_box,
        ):
            if event["type"] == "reset":
                parts.clear()
            elif event["type"] == "token":
                parts.append(event["content"])
            yield _sse(event)
        reply = "".join(parts).strip() or ANSWER_FALLBACK
    except Exception:
        if parts:
            yield _sse({"type": "reset"})
        reply = ANSWER_FALLBACK
        lookup_box["result"] = None
        yield _sse({"type": "token", "content": reply})
    saved = _persist_bot_reply(
        db,
        conversation,
        req,
        rewritten,
        reply,
        lookup_payload(lookup_box["result"]),
        commit=False,
    )
    if needs_refusal_handoff(recent, reply):
        category, summary = fill_ticket(req.message, complete)
        ticket = _open_ticket(db, conversation, "normal", category, summary)
        db.commit()
        if parts:
            yield _sse({"type": "reset"})
        yield _sse({"type": "status", "message": "正在转人工"})
        payload = {
            "type": "ticket_created",
            "ticket_id": ticket.id,
            "mode": "human",
            "priority": ticket.priority,
        }
        yield _sse(payload)
        yield _sse(
            {
                "type": "done",
                "final_answer": saved.assistant_message or "已转接人工客服",
                "ticket_id": ticket.id,
                "mode": "human",
                "rewritten_query": None,
            }
        )
        return
    db.commit()
    yield _sse(
        {
            "type": "done",
            "final_answer": saved.assistant_message or reply,
            "ticket_id": None,
            "mode": saved.mode,
            "rewritten_query": saved.rewritten_query,
        }
    )


@router.post("/chat/stream")
def chat_stream(
    req: ChatRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    complete: Completer,
    embed: Embedder,
) -> StreamingResponse:
    conversation = db.get(Conversation, req.conversation_id)
    if conversation is None or conversation.customer_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not allow_chat(user.id):
        raise HTTPException(status_code=429, detail="消息太频繁，请稍后再发")
    if conversation.mode == "human" or triage_update(req.message).get("handoff"):
        result = chat(req, user, db, complete, embed)
        return StreamingResponse(
            _finished_events(db, req, result), media_type="text/event-stream"
        )

    history = [
        {"role": m.role, "content": m.content}
        for m in db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at)
        .all()
    ]
    recent = history[-10:]
    slots_row = db.get(ConversationSlot, conversation.id)
    current = (
        {
            "last_order_id": slots_row.last_order_id,
            "last_product_id": slots_row.last_product_id,
            "last_phone": slots_row.last_phone,
            "last_name": slots_row.last_name,
            "candidate_order_ids": slots_row.candidate_order_ids,
        }
        if slots_row
        else {}
    )
    found = extract_slots(req.message)
    merged = merge_slots(current, found)
    merged["candidate_order_ids"] = current.get("candidate_order_ids")
    if slots_row is None:
        slots_row = ConversationSlot(conversation_id=conversation.id)
        db.add(slots_row)
    slots_row.last_product_id = merged.get("last_product_id")
    slots_row.last_phone = merged.get("last_phone")
    slots_row.last_name = merged.get("last_name")
    try:
        rewritten = rewrite_query(req.message, recent, merged, complete)
    except Exception:
        rewritten = req.message
    return StreamingResponse(
        _bot_events(req, user, db, complete, embed, conversation, recent, merged, rewritten),
        media_type="text/event-stream",
    )


@router.post("/tickets/{ticket_id}/claim", response_model=ClaimResponse)
def claim_ticket(
    ticket_id: str,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ClaimResponse:
    if user.role != "agent":
        raise HTTPException(status_code=403, detail="Only agents can claim tickets")
    exists = db.scalar(select(Ticket.id).where(Ticket.id == ticket_id))
    if exists is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if not claim_if_open(db, ticket_id, user.id):
        db.rollback()
        raise HTTPException(status_code=409, detail="Ticket is not open")
    db.add(
        TicketEvent(
            ticket_id=ticket_id,
            actor_id=user.id,
            type="claim",
            from_status="open",
            to_status="in_progress",
        )
    )
    db.commit()
    publish("refresh")
    return ClaimResponse(status="in_progress")


@router.get("/tickets", response_model=TicketListResponse)
def list_tickets(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    status: str = "open",
) -> TicketListResponse:
    _require_agent(user)
    rank = case(
        (Ticket.priority == "high", 0),
        (Ticket.priority == "normal", 1),
        (Ticket.priority == "low", 2),
        else_=3,
    )
    rows = (
        db.query(Ticket)
        .filter(Ticket.status == status)
        .order_by(rank, Ticket.created_at)
        .all()
    )
    return TicketListResponse(tickets=[_ticket_info(row) for row in rows])


@router.get("/tickets/events")
def ticket_events(
    user: CurrentUser,
    request: Request,
):
    _require_agent(user)
    pubsub = listen()

    def stream():
        if pubsub is None:
            yield "data: fallback\n\n"
            return
        try:
            for item in pubsub.listen():
                if request.is_disconnected():
                    break
                if item.get("type") == "message":
                    data = item.get("data")
                    text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
                    yield f"data: {text}\n\n"
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
def get_ticket(
    ticket_id: str,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> TicketDetailResponse:
    _require_agent(user)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == ticket.conversation_id)
        .order_by(Message.created_at)
        .all()
    )
    events = db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id).all()
    conversation = db.get(Conversation, ticket.conversation_id)
    profile_row = get_profile(db, conversation.customer_id) if conversation else None
    profile = None
    if profile_row:
        has_profile = any(
            [
                profile_row.name,
                profile_row.phone,
                profile_row.last_product_id,
                profile_row.last_ticket_summary,
            ]
        )
        if has_profile:
            profile = ProfileInfo(
                name=profile_row.name,
                phone=profile_row.phone,
                last_product_id=profile_row.last_product_id,
                last_ticket_summary=profile_row.last_ticket_summary,
            )
    return TicketDetailResponse(
        ticket=_ticket_info(ticket),
        messages=[
            MessageInfo(
                id=item.id,
                role=item.role,
                content=item.content,
                created_at=item.created_at,
            )
            for item in messages
        ],
        events=[
            EventInfo(
                id=item.id,
                type=item.type,
                actor_id=item.actor_id,
                from_status=item.from_status,
                to_status=item.to_status,
            )
            for item in events
        ],
        profile=profile,
    )


@router.post("/tickets/{ticket_id}/messages", response_model=AgentMessageResponse)
def reply_ticket(
    ticket_id: str,
    req: AgentMessageRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> AgentMessageResponse:
    _require_agent(user)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.status != "in_progress":
        raise HTTPException(status_code=409, detail="Ticket is not in progress")
    if ticket.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="Only the assignee can reply")
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message content is required")
    message = Message(
        conversation_id=ticket.conversation_id,
        role="agent",
        content=content,
    )
    db.add(message)
    db.commit()
    publish("refresh")
    return AgentMessageResponse(message_id=message.id)


@router.post("/tickets/{ticket_id}/transition", response_model=ClaimResponse)
def transition_ticket(
    ticket_id: str,
    req: TransitionRequest,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ClaimResponse:
    _require_agent(user)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        new_status = transition(ticket.status, req.action)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail="Illegal ticket transition") from exc
    old_status = ticket.status
    ticket.status = new_status
    db.add(
        TicketEvent(
            ticket_id=ticket.id,
            actor_id=user.id,
            type=req.action,
            from_status=old_status,
            to_status=new_status,
        )
    )
    if new_status == "closed":
        conversation = db.get(Conversation, ticket.conversation_id)
        if conversation:
            conversation.mode = "bot"
            slots_row = db.get(ConversationSlot, conversation.id)
            remember_profile(
                db,
                conversation.customer_id,
                name=slots_row.last_name if slots_row else None,
                phone=slots_row.last_phone if slots_row else None,
                last_product_id=slots_row.last_product_id if slots_row else None,
                last_ticket_summary=ticket.summary or None,
            )
            note = ticket.summary or "人工已结案"
            db.add(
                Message(
                    conversation_id=conversation.id,
                    role="system",
                    content=f"人工已处理：{note}",
                )
            )
    db.commit()
    publish("refresh")
    return ClaimResponse(status=new_status)
