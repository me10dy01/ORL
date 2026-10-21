"""查询改写与槽位提取：让「这个订单到哪了」变成可执行的独立问句。

两件事：
1. extract_slots——用纯正则从用户原话里抠出订单号/商品号/手机号/姓名。
   关键安全设计：只信用户自己的话，不信模型输出（模型可能幻觉出 ID）。
2. rewrite_query——把「这个/它」等指代消解外包给 LLM，但提供历史和已确认槽位做依据。

面试考点：为什么槽位用正则而不用 NER 模型（确定性、可测试、零延迟）；
merge_slots 的「后写覆盖」语义如何构成会话记忆。
"""

import re

# 订单号/商品号是系统内强格式 ID，正则匹配足够且零误判成本。
ORDER_RE = re.compile(r"ORD\d{8}")
PRODUCT_RE = re.compile(r"P\d{3}")
# 手机号：负向环视确保前后不是数字，避免从更长的数字串里误切一段。
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 姓名只从「我叫XX」「姓名是XX」这类显式自我介绍里取——宁可漏，不可错。
NAME_RE = re.compile(r"(?:我叫|姓名是)([\u4e00-\u9fff]{2,4})")
# 会话槽位的固定键集合，与 ConversationSlot 表的列一一对应。
SLOT_KEYS = (
    "last_order_id",
    "last_product_id",
    "last_phone",
    "last_name",
)


def extract_slots(text: str) -> dict[str, str]:
    """从用户原话提取标识符。安全前提：只读 user 消息，绝不读模型输出。"""
    slots: dict[str, str] = {}
    if m := ORDER_RE.search(text):
        slots["last_order_id"] = m.group(0)
    if m := PRODUCT_RE.search(text):
        slots["last_product_id"] = m.group(0)
    if m := PHONE_RE.search(text):
        slots["last_phone"] = m.group(0)
    if m := NAME_RE.search(text):
        slots["last_name"] = m.group(1)
    return slots


def merge_slots(current: dict, found: dict) -> dict:
    """本轮新提取的槽位覆盖旧值（last-write-wins），形成「最近谈到」的记忆。"""
    merged = dict(current)
    for key in SLOT_KEYS:
        value = found.get(key)
        if value:
            merged[key] = value
    return merged


def rewrite_query(current: str, history: list[dict], slots: dict, complete) -> str:
    """指代消解：首轮且无槽位时直接放行（省时延）；改写失败/为空回退原话。

    为什么不信任改写结果做兜底：模型可能输出客套话或空串，原话永远合法。
    """
    has_slots = any(slots.get(key) for key in SLOT_KEYS)
    if not history and not has_slots:
        return current
    lines = [f"{m.get('role')}: {m.get('content')}" for m in history]
    prompt = (
        "把用户当前问题改写成独立问句，消解「这个」「它」等指代。"
        "不要回答。若已完整则原样输出。\n"
        f"槽位：{slots}\n历史：\n" + "\n".join(lines) + f"\n当前：{current}\n改写："
    )
    rewritten = (complete(prompt) or "").strip()
    return rewritten or current
