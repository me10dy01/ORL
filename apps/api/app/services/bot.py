"""Prompt 模板与业务话术常量：所有「和模型说的话」集中在这一层。

面试考点：
- 转人工关键词分流为什么用关键词而不是分类模型（确定性、可审计、零延迟）；
- 拒答话术 REFUSAL_SENTENCES 必须与 prompt 里指示的回复逐字一致，否则检测失效；
- parse_prefill 的「永不信任模型格式」兜底设计。
"""

# 关键词分流表：投诉/退款类直接开高优工单，「转人工」开普通工单。
COMPLAINT_MARKERS = ("投诉", "纠纷")
REFUND_REQUESTS = ("我要退款", "申请退款", "帮我退款", "给我退款")
HANDOFF_MARKERS = ("转人工", "人工客服")
# 拒答判定：模型被指示「没有依据时只回复：知识库暂无依据」，
# 这里做整句精确匹配——prompt 文案和检测常量必须同步修改。
REFUSAL_SENTENCES = ("无法回答", "知识库暂无依据")
# 全链路统一兜底文案：模型异常/空回复/超轮数时给用户的最后一句话。
ANSWER_FALLBACK = "抱歉，当前服务暂时不可用，请稍后再试。"


def handoff_priority(text: str) -> str | None:
    """Return ticket priority when the user message should open a ticket.

    顺序有意义：先匹配 high（投诉/退款），再匹配 normal（主动要人工）。
    """
    if any(marker in text for marker in COMPLAINT_MARKERS):
        return "high"
    if any(marker in text for marker in REFUND_REQUESTS):
        return "high"
    if any(marker in text for marker in HANDOFF_MARKERS):
        return "normal"
    return None


def is_refusal(text: str) -> bool:
    """整句精确匹配，避免把「知识库暂无依据，但我建议…」这类半拒答误判。"""
    return (text or "").strip() in REFUSAL_SENTENCES


def consecutive_refusals(previous_assistant: list[str], newest: str) -> int:
    """从最新回复往前数连续拒答的轮数；中间夹一条正常回复就清零。"""
    if not is_refusal(newest):
        return 0
    count = 1
    for content in reversed(previous_assistant):
        if not is_refusal(content):
            break
        count += 1
    return count


def profile_line(profile) -> str:
    """把用户画像压成一行，注入 prompt 首条消息，让模型跨会话认出老顾客。"""
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
    """工具选择轮的 prompt：「回答前先调用工具」强制走 ReAct 路径，先查再答。"""
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
    """工具结果回灌后的约束：只依据工具数据回答，防止模型自由发挥编造。"""
    return "只根据工具返回的内容回答。没有依据时只回复：知识库暂无依据"


def answer_prompt(
    rewritten: str,
    history: list[dict],
    knowledge: list[str] | None = None,
    business: str = "",
) -> str:
    """非工具路径的兜底 prompt（测试里的假模型走这条路，见 tool_loop 能力探测）。"""
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
    """让模型把用户消息预填成工单字段，约定「两行键值对」的弱格式协议。"""
    return (
        "根据用户消息输出两行：\ncategory: 简短分类\nsummary: 一句话摘要\n"
        f"用户消息：{message}"
    )


def parse_prefill(raw: str, fallback_summary: str) -> tuple[str, str]:
    """解析两行协议。永不信任模型格式：缺行/空值就回退默认分类+用户原话。"""
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
