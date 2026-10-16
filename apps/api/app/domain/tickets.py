class InvalidTransition(Exception):
    pass


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
