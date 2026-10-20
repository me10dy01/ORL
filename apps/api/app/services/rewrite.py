import re

ORDER_RE = re.compile(r"ORD\d{8}")
PRODUCT_RE = re.compile(r"P\d{3}")
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
NAME_RE = re.compile(r"(?:我叫|姓名是)([\u4e00-\u9fff]{2,4})")
SLOT_KEYS = (
    "last_order_id",
    "last_product_id",
    "last_phone",
    "last_name",
)


def extract_slots(text: str) -> dict[str, str]:
    """Read identifiers from the user's own words, not from model output."""
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
    merged = dict(current)
    for key in SLOT_KEYS:
        value = found.get(key)
        if value:
            merged[key] = value
    return merged


def rewrite_query(current: str, history: list[dict], slots: dict, complete) -> str:
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
