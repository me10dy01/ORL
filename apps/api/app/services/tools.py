"""订单与库存查询：业务工具的实现层，安全边界全在这里。

lookup_context 是订单查询的「归属判定器」——决定哪些订单可以展示、展示到什么粒度。
面试考点：订单归属校验的层级设计、金额为什么只在单笔授权时显示、
「手机号+姓名」双因子查单的业务场景（帮家人查/未注册下单）。
"""

from sqlalchemy.orm import Session

from app.models import OrderRecord, Product
from app.services.rewrite import extract_slots

_LIST_HINT = "请选择其中一单"
# 贪心子串匹配时要跳过的「噪音词」——它们出现在任何句子里，不代表在聊商品。
_SKIP_PIECES = {"那单", "这个", "订单", "我的", "到哪", "一下", "状态"}


def lookup_context(
    db: Session, slots: dict, user_id: str, message: str
) -> tuple[str, str | None, list[str]]:
    """Return prompt text, one authorized order id, and the current candidate ids.

    Order ids are taken from ``message`` only. A stored id is reused only after
    another ownership check. Amounts are included only for a single authorized order.

    判定优先级（从高到低，命中即返回）：
    1. 用户本轮报了订单号 → 查库 + _allows 归属校验
    2. 用户报了手机号+姓名 → 双因子查单（支持帮他人查/未注册单）
    3. 有候选列表 + 用户提到了其中某单的商品 → 缩小到单笔
    4. 会话记住了 last_order_id → 重查归属后展示
    5. 兜底：列出本人全部订单
    """
    spoken = extract_slots(message)
    candidates = _parse_candidates(slots.get("candidate_order_ids"))

    # 分支1：用户报了订单号。即使报的是别人的单，也走 _allows 判定而不是直接拒绝——
    # 因为「手机号+姓名」双因子匹配同样算授权。
    if spoken.get("last_order_id"):
        order = db.get(OrderRecord, spoken["last_order_id"])
        if order is not None and _allows(order, user_id, spoken, candidates):
            return _with_product(db, slots, _detail(db, order), order.id, [])
        return _with_product(db, slots, "未找到", None, [])

    # 分支2：手机号+姓名双因子——订单可以不属于当前账号（user_id 可能为空）。
    if spoken.get("last_phone") and spoken.get("last_name"):
        rows = _orders_for_identity(db, spoken["last_phone"], spoken["last_name"])
        text, order_id, found_ids = _format_rows(db, rows)
        return _with_product(db, slots, text, order_id, found_ids)

    # 分支3：候选列表消歧。上轮列出了多单，这轮用户说「键盘那单」，
    # 用商品名做贪心匹配定位到唯一一单后才放行。
    if candidates and "我的订单" not in message:
        matched = [
            order
            for order in _load(db, candidates)
            if _product_mentioned(message, _product_name(db, order))
        ]
        if len(matched) == 1 and _allows(matched[0], user_id, spoken, candidates):
            return _with_product(db, slots, _detail(db, matched[0]), matched[0].id, [])

    # 分支4：会话记忆里的订单。跨轮复用前必须重查归属——账号可能已切换。
    remembered = slots.get("last_order_id")
    if (
        remembered
        and "我的订单" not in message
        and not spoken.get("last_phone")
        and not spoken.get("last_name")
    ):
        order = db.get(OrderRecord, remembered)
        if order is not None and order.user_id == user_id:
            return _with_product(db, slots, _detail(db, order), order.id, [])
        return _with_product(db, slots, "未找到", None, [])

    # 分支5：兜底列本人全部订单，供下一轮消歧。
    rows = (
        db.query(OrderRecord)
        .filter(OrderRecord.user_id == user_id)
        .order_by(OrderRecord.created_at.desc())
        .all()
    )
    text, order_id, found_ids = _format_rows(db, rows)
    return _with_product(db, slots, text, order_id, found_ids)


def _with_product(db, slots, text, order_id, found_ids):
    """顺带附上本轮提到的商品库存行；查单失败时只回库存，不叠加错误文案。"""
    catalog = _product_line(db, slots.get("last_product_id"))
    if not catalog:
        return text, order_id, found_ids
    if text == "未找到":
        return catalog, order_id, found_ids
    return f"{text}\n{catalog}", order_id, found_ids


def _allows(order: OrderRecord, user_id: str, spoken: dict, candidates: list[str]) -> bool:
    """归属校验：本人订单直接放行；否则要求手机号+姓名双因子同时匹配。"""
    if order.user_id == user_id:
        return True
    if order.id in candidates and order.user_id == user_id:
        return True
    return (
        bool(spoken.get("last_phone") and spoken.get("last_name"))
        and order.phone == spoken["last_phone"]
        and order.customer_name == spoken["last_name"]
    )


def _orders_for_identity(db: Session, phone: str, name: str) -> list[OrderRecord]:
    return (
        db.query(OrderRecord)
        .filter(OrderRecord.phone == phone, OrderRecord.customer_name == name)
        .order_by(OrderRecord.created_at.desc())
        .all()
    )


def _format_rows(
    db: Session, rows: list[OrderRecord]
) -> tuple[str, str | None, list[str]]:
    """多单列表只暴露 id/商品/状态，不暴露金额——列表可被非归属路径触发。"""
    if not rows:
        return "未找到", None, []
    if len(rows) == 1:
        return _detail(db, rows[0]), rows[0].id, []
    lines = [_LIST_HINT]
    for order in rows:
        lines.append(
            f"订单 {order.id} {_product_name(db, order)} 状态 {order.status}"
        )
    return "\n".join(lines), None, [order.id for order in rows]


def _detail(db: Session, order: OrderRecord) -> str:
    """单笔详情（含金额）——只有经过 _allows 授权的单笔才会走到这里。"""
    amount = f"{order.amount:.2f}"
    text = (
        f"订单 {order.id} {_product_name(db, order)} 状态 {order.status}，金额 {amount}"
    )
    return text


def _product_name(db: Session, order: OrderRecord) -> str:
    if not order.product_id:
        return ""
    product = db.get(Product, order.product_id)
    return product.name if product else ""


def lookup_stock(db: Session, query: str) -> str:
    """Return public stock lines for a product id or name. Errors become 未找到.

    库存是公开信息，不需要归属校验；任何异常都降级为「未找到」，
    单工具失败不拖垮整个对话。
    """
    try:
        return _lookup_stock(db, query)
    except Exception:
        return "未找到"


def _lookup_stock(db: Session, query: str) -> str:
    text = (query or "").strip()
    if not text:
        return "未找到"
    from app.services.rewrite import PRODUCT_RE

    # 先按商品号精确查（P001 这种），查不到再按名字模糊匹配。
    ids = list(dict.fromkeys(PRODUCT_RE.findall(text)))
    if ids:
        found = [product for product in (db.get(Product, product_id) for product_id in ids) if product is not None]
        found.sort(key=lambda product: product.id)
        if not found:
            return "未找到"
        return "\n".join(_stock_line(product) for product in found)
    matched = [
        product
        for product in db.query(Product).order_by(Product.id).all()
        if _name_hit(text, product.name)
    ]
    if not matched:
        return "未找到"
    return "\n".join(_stock_line(product) for product in matched)


def _stock_line(product: Product) -> str:
    return f"商品 {product.id} {product.name}，库存 {product.stock}"


def _name_hit(query: str, name: str) -> bool:
    if not name:
        return False
    if query in name or name in query:
        return True
    return _product_mentioned(query, name)


def _product_line(db: Session, product_id: str | None) -> str:
    if not product_id:
        return ""
    product = db.get(Product, product_id)
    if product is None:
        return "未找到"
    return f"商品 {product.id} {product.name}，库存 {product.stock}"


def _product_mentioned(message: str, product_name: str) -> bool:
    """贪心最长匹配：从最长子串开始试，命中商品名即认为「在聊这个商品」。

    是 O(n²·m) 的暴力法，但消息和商品名都很短，确定性比效率重要。
    """
    if not product_name:
        return False
    for size in range(min(len(message), len(product_name)), 1, -1):
        for start in range(len(message) - size + 1):
            piece = message[start : start + size]
            if piece in _SKIP_PIECES:
                continue
            if piece in product_name:
                return True
    return False


def _parse_candidates(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item for item in raw.split(",") if item]


def _load(db: Session, order_ids: list[str]) -> list[OrderRecord]:
    """按候选列表顺序返回订单（IN 查询不保证顺序，用 dict 重排）。"""
    rows = db.query(OrderRecord).filter(OrderRecord.id.in_(order_ids)).all()
    by_id = {row.id: row for row in rows}
    return [by_id[order_id] for order_id in order_ids if order_id in by_id]
