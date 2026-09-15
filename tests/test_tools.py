"""Phase 03 验收测试：Tool 可独立测试。

覆盖：正常查询、参数缺失、参数非法、数据不存在、空结果。
依赖本机 MySQL（通过 tests/conftest.py 的 initialized_db fixture 初始化）。
"""

from __future__ import annotations

import pytest

from app.tools import (
    get_inventory,
    get_order,
    get_order_items,
    get_product_info,
    get_user_orders,
    search_products,
)

pytestmark = pytest.mark.usefixtures("initialized_db")


# ---- get_product_info ----

def test_get_product_info_found() -> None:
    r = get_product_info("P001")
    assert r.ok
    assert r.data["name"] == "星辰 K87 机械键盘"
    assert r.data["price"] == "299.00"
    assert r.data["category"] == "键盘"


def test_get_product_info_not_found() -> None:
    r = get_product_info("P999")
    assert not r.ok
    assert r.error_type == "not_found"


def test_get_product_info_invalid_format() -> None:
    r = get_product_info("abc")
    assert not r.ok
    assert r.error_type == "validation_error"


def test_get_product_info_missing() -> None:
    r = get_product_info("")
    assert not r.ok
    assert r.error_type == "validation_error"


# ---- get_inventory ----

def test_get_inventory_found() -> None:
    r = get_inventory("P003")
    assert r.ok
    assert r.data[0]["quantity"] == 45
    assert r.data[0]["warehouse"] == "华东仓"


def test_get_inventory_not_found() -> None:
    r = get_inventory("P999")
    assert not r.ok
    assert r.error_type == "not_found"


# ---- get_order ----

def test_get_order_found() -> None:
    r = get_order("ORD20260001")
    assert r.ok
    assert r.data["status"] == "已发货"
    assert r.data["total_amount"] == "428.00"


def test_get_order_not_found() -> None:
    r = get_order("ORD99999999")
    assert not r.ok
    assert r.error_type == "not_found"


def test_get_order_invalid_format() -> None:
    r = get_order("ORD123")
    assert not r.ok
    assert r.error_type == "validation_error"


# ---- get_order_items ----

def test_get_order_items_found() -> None:
    r = get_order_items("ORD20260006")
    assert r.ok
    assert len(r.data) == 2
    product_ids = {item["product_id"] for item in r.data}
    assert product_ids == {"P007", "P001"}


def test_get_order_items_not_found() -> None:
    r = get_order_items("ORD99999999")
    assert not r.ok
    assert r.error_type == "not_found"


# ---- get_user_orders ----

def test_get_user_orders_found() -> None:
    r = get_user_orders("U0002")
    assert r.ok
    assert len(r.data) == 2
    order_ids = {o["order_id"] for o in r.data}
    assert order_ids == {"ORD20260003", "ORD20260005"}


def test_get_user_orders_empty() -> None:
    r = get_user_orders("U0006")
    assert r.ok
    assert r.data == []


def test_get_user_orders_not_found() -> None:
    r = get_user_orders("U9999")
    assert not r.ok
    assert r.error_type == "not_found"


# ---- search_products ----

def test_search_products_found() -> None:
    r = search_products("K87")
    assert r.ok
    assert any(p["product_id"] == "P001" for p in r.data)


def test_search_products_no_result() -> None:
    r = search_products("不存在的商品XYZ")
    assert r.ok
    assert r.data == []


def test_search_products_missing() -> None:
    r = search_products("")
    assert not r.ok
    assert r.error_type == "validation_error"
