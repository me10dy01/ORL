"""Phase 02 验收测试：数据库（种子数据）、知识库、测试集逻辑一致。

验证点：
1. 种子数据内部一致（主键唯一、外键有效、订单金额 = 明细求和）。
2. 测试问题集中引用的实体（P*/ORD*/U*）均存在于种子数据。
3. 知识库文档中出现的商品型号均存在于商品数据。
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.seed import (
    DOCUMENTS_DIR,
    load_inventory,
    load_order_items,
    load_orders,
    load_products,
    load_test_questions,
    load_users,
)

# 商品型号形如 K87 / M30 / H50 / P10 / M27 / S30 / K60 等
MODEL_CODE_RE = re.compile(r"\b[A-Z]\d{2}\b")


def test_products_have_unique_ids() -> None:
    products = load_products()
    ids = [p.product_id for p in products]
    assert len(ids) == len(set(ids)), "商品 product_id 重复"
    assert len(products) > 0


def test_users_have_unique_ids() -> None:
    users = load_users()
    ids = [u.user_id for u in users]
    assert len(ids) == len(set(ids)), "用户 user_id 重复"


def test_inventory_references_existing_products() -> None:
    products = {p.product_id for p in load_products()}
    inventory = load_inventory()
    for inv in inventory:
        assert inv.product_id in products, f"库存引用了不存在的商品: {inv.product_id}"
    # 每个商品至少有一条库存记录
    inv_products = {inv.product_id for inv in inventory}
    assert products <= inv_products, f"缺少库存记录的商品: {products - inv_products}"


def test_orders_reference_existing_users() -> None:
    users = {u.user_id for u in load_users()}
    orders = load_orders()
    order_ids = [o.order_id for o in orders]
    assert len(order_ids) == len(set(order_ids)), "订单 order_id 重复"
    for o in orders:
        assert o.user_id in users, f"订单 {o.order_id} 引用了不存在的用户: {o.user_id}"


def test_order_items_reference_existing_orders_and_products() -> None:
    orders = {o.order_id for o in load_orders()}
    products = {p.product_id for p in load_products()}
    items = load_order_items()
    item_ids = [i.order_item_id for i in items]
    assert len(item_ids) == len(set(item_ids)), "明细 order_item_id 重复"
    for item in items:
        assert item.order_id in orders, f"明细引用了不存在的订单: {item.order_id}"
        assert item.product_id in products, f"明细引用了不存在的商品: {item.product_id}"


def test_order_total_matches_items_sum() -> None:
    orders = {o.order_id: o for o in load_orders()}
    items = load_order_items()
    sums: dict[str, Decimal] = {}
    for item in items:
        sums[item.order_id] = sums.get(item.order_id, Decimal("0")) + (
            item.unit_price * item.quantity
        )
    for order_id, order in orders.items():
        expected = sums.get(order_id, Decimal("0"))
        assert order.total_amount == expected, (
            f"订单 {order_id} 金额 {order.total_amount} 与明细合计 {expected} 不一致"
        )


def test_test_questions_reference_valid_entities() -> None:
    products = {p.product_id for p in load_products()}
    users = {u.user_id for u in load_users()}
    orders = {o.order_id for o in load_orders()}

    questions = load_test_questions()
    assert len(questions) > 0
    ids = [q.id for q in questions]
    assert len(ids) == len(set(ids)), "测试问题 id 重复"

    # negative / error 类问题故意引用不存在的实体，用于验证"数据不存在"场景
    exempt_categories = {"negative", "error"}

    for q in questions:
        for entity in q.expected_entities:
            if q.category in exempt_categories:
                continue
            if entity.startswith("ORD"):
                assert entity in orders, f"{q.id} 引用了不存在的订单: {entity}"
            elif entity.startswith("P"):
                assert entity in products, f"{q.id} 引用了不存在的商品: {entity}"
            elif entity.startswith("U"):
                assert entity in users, f"{q.id} 引用了不存在的用户: {entity}"
            else:
                raise AssertionError(f"{q.id} 包含无法识别的实体: {entity}")


def test_test_questions_cover_all_categories() -> None:
    questions = load_test_questions()
    categories = {q.category for q in questions}
    required = {
        "rag",
        "tool",
        "mixed",
        "multi_turn",
        "negative",
        "error",
        "ambiguous",
        "hard_retrieval",
        "tool_exception",
        "rag_no_answer",
    }
    assert required <= categories, f"测试集缺少类别: {required - categories}"


def test_documents_mention_consistent_product_codes() -> None:
    """知识库文档中出现的商品型号必须存在于商品数据中。"""
    products = load_products()
    known_codes: set[str] = set()
    for p in products:
        known_codes.update(MODEL_CODE_RE.findall(p.name))

    assert known_codes, "未能从商品名称中提取到型号"

    docs = sorted(DOCUMENTS_DIR.glob("*.md"))
    assert len(docs) >= 1, "知识库文档缺失"

    unknown_codes: set[str] = set()
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for code in MODEL_CODE_RE.findall(text):
            if code not in known_codes:
                unknown_codes.add(code)

    assert not unknown_codes, f"知识库文档引用了未知商品型号: {unknown_codes}"


def test_documents_reference_k87() -> None:
    """核心示例商品 K87 应出现在知识库中，保证与 AGENTS.md 示例一致。"""
    texts = [doc.read_text(encoding="utf-8") for doc in DOCUMENTS_DIR.glob("*.md")]
    assert any("K87" in t for t in texts), "知识库文档中未出现核心示例商品 K87"
