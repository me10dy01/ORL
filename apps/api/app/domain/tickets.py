class InvalidTransition(Exception):
    pass


def claim_if_open(db, ticket_id: str, assignee_id: str) -> bool:
    from sqlalchemy import update

    from app.models import Ticket

    result = db.execute(
        update(Ticket)
        .where(Ticket.id == ticket_id, Ticket.status == "open")
        .values(status="in_progress", assignee_id=assignee_id),
        execution_options={"synchronize_session": False},
    )
    return result.rowcount == 1


_EDGES: dict[tuple[str, str], str] = {
    ("open", "claim"): "in_progress",
    ("open", "cancel"): "cancelled",
    ("in_progress", "resolve"): "resolved",
    ("in_progress", "cancel"): "cancelled",
    ("resolved", "close"): "closed",
}


def transition(status: str, action: str) -> str:
    nxt = _EDGES.get((status, action))
    if nxt is None:
        raise InvalidTransition(f"{status} cannot {action}")
    return nxt
