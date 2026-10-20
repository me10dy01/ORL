COMPLAINT_MARKERS = ("投诉", "纠纷")
REFUND_REQUESTS = ("我要退款", "申请退款", "帮我退款", "给我退款")
HANDOFF_MARKERS = ("转人工", "人工客服")
REFUSAL_SENTENCES = ("无法回答", "知识库暂无依据")
ANSWER_FALLBACK = "抱歉，当前服务暂时不可用，请稍后再试。"


def handoff_priority(text: str) -> str | None:
    """Return ticket priority when the user message should open a ticket."""
    if any(marker in text for marker in COMPLAINT_MARKERS):
        return "high"
    if any(marker in text for marker in REFUND_REQUESTS):
        return "high"
    if any(marker in text for marker in HANDOFF_MARKERS):
        return "normal"
    return None


def is_refusal(text: str) -> bool:
    return (text or "").strip() in REFUSAL_SENTENCES


def consecutive_refusals(previous_assistant: list[str], newest: str) -> int:
    if not is_refusal(newest):
        return 0
    count = 1
    for content in reversed(previous_assistant):
        if not is_refusal(content):
            break
        count += 1
    return count


def profile_line(profile) -> str:
    if not profile:
        return ""
    parts = []
    if profile.name:
        parts.append(f"姓名 {profile.name}")
    if profile.phone:
        parts.append(f"手机 {profile.phone}")
    if profile.last_product_id:
        parts.append(f"最近商品 {profile.last_product_id}")
    if profile.last_ticket_summary:
        parts.append(f"上一张工单 {profile.last_ticket_summary}")
    return "；".join(parts)


def tool_choice_prompt(rewritten: str, history: list[dict], profile_line_text: str = "") -> str:
    recent = history[-10:]
    lines = "\n".join(f"{item.get('role')}: {item.get('content')}" for item in recent)
    known = f"已知顾客：{profile_line_text}\n" if profile_line_text else ""
    return (
        "你是星辰数码商城客服。回答前先调用工具。\n"
        "制度问题调用 search_knowledge，订单问题调用 lookup_orders，库存问题调用 lookup_stock。\n"
        f"{known}"
        f"最近对话：\n{lines}\n问题：{rewritten}\n"
    )


def answer_from_tools_prompt() -> str:
    return "只根据工具返回的内容回答。没有依据时只回复：知识库暂无依据"


def answer_prompt(
    rewritten: str,
    history: list[dict],
    knowledge: list[str] | None = None,
    business: str = "",
) -> str:
    recent = history[-10:]
    lines = "\n".join(f"{item.get('role')}: {item.get('content')}" for item in recent)
    knowledge_text = "\n".join(knowledge) if knowledge else "没有检索到知识"
    business_text = business or "未找到"
    return (
        "你是星辰数码商城客服。只根据下面的知识库、业务数据和最近对话回答问题。"
        "没有依据时只回复：知识库暂无依据\n"
        f"知识库：\n{knowledge_text}\n\n"
        f"业务数据：\n{business_text}\n\n"
        f"最近对话：\n{lines}\n问题：{rewritten}\n回答："
    )


def prefill_prompt(message: str) -> str:
    return (
        "根据用户消息输出两行：\ncategory: 简短分类\nsummary: 一句话摘要\n"
        f"用户消息：{message}"
    )


def parse_prefill(raw: str, fallback_summary: str) -> tuple[str, str]:
    category = "general"
    summary = fallback_summary
    for line in raw.splitlines():
        lower = line.lower()
        if lower.startswith("category:"):
            value = line.split(":", 1)[1].strip()
            if value:
                category = value
        elif lower.startswith("summary:"):
            value = line.split(":", 1)[1].strip()
            if value:
                summary = value
    return category, summary
