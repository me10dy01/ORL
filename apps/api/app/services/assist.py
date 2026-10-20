def suggest_reply(messages: list[dict], summary: str, complete) -> str:
    """Draft one agent reply. Failure returns an empty string and does not block the ticket."""
    recent = messages[-10:]
    lines = "\n".join(f"{item.get('role')}: {item.get('content')}" for item in recent)
    prompt = (
        "你是坐席助手。根据工单摘要和最近对话，写一条坐席可以直接发给顾客的回复。"
        "只输出回复正文。\n"
        f"摘要：{summary or '无'}\n"
        f"最近对话：\n{lines}\n"
        "回复："
    )
    try:
        return (complete(prompt) or "").strip()
    except Exception:
        return ""
