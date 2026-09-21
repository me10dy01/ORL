"""业务数据查询。

链路：Tool（参数校验）→ 本模块 → Repository → MySQL。
这里只把 ORM 对象转成 dict，并把 SQLAlchemy 异常转成 DatabaseError。
HTTP 用例在 app.application，不要和本模块混用「服务」这个名字。
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from app import db
from app import repositories as repo
from app.exceptions import DatabaseError, NotFoundError


def _product_to_dict(p: db.Product) -> dict:
    return {
        "product_id": p.product_id,
        "sku": p.sku,
        "name": p.name,
        "category": p.category,
        "price": str(p.price),
        "description": p.description,
    }


def _inventory_to_dict(i: db.Inventory) -> dict:
    return {"product_id": i.product_id, "warehouse": i.warehouse, "quantity": i.quantity}


def _order_to_dict(o: db.Order) -> dict:
    return {
        "order_id": o.order_id,
        "user_id": o.user_id,
        "status": o.status,
        "total_amount": str(o.total_amount),
        "created_at": o.created_at.isoformat(),
    }


def _order_item_to_dict(i: db.OrderItem) -> dict:
    return {
        "order_item_id": i.order_item_id,
        "order_id": i.order_id,
        "product_id": i.product_id,
        "quantity": i.quantity,
        "unit_price": str(i.unit_price),
    }


def get_product_info(product_id: str) -> dict:
    try:
        with db.session_scope() as session:
            product = repo.get_product_by_id(session, product_id)
            if product is None:
                raise NotFoundError(f"商品不存在: {product_id}")
            return _product_to_dict(product)
    except SQLAlchemyError as exc:
        raise DatabaseError(f"数据库异常: {exc}") from exc


def get_inventory(product_id: str) -> list[dict]:
    try:
        with db.session_scope() as session:
            if repo.get_product_by_id(session, product_id) is None:
                raise NotFoundError(f"商品不存在: {product_id}")
            rows = repo.get_inventory_by_product(session, product_id)
            return [_inventory_to_dict(r) for r in rows]
    except SQLAlchemyError as exc:
        raise DatabaseError(f"数据库异常: {exc}") from exc


def get_order(order_id: str) -> dict:
    try:
        with db.session_scope() as session:
            order = repo.get_order_by_id(session, order_id)
            if order is None:
                raise NotFoundError(f"订单不存在: {order_id}")
            return _order_to_dict(order)
    except SQLAlchemyError as exc:
        raise DatabaseError(f"数据库异常: {exc}") from exc


def get_order_items(order_id: str) -> list[dict]:
    try:
        with db.session_scope() as session:
            if repo.get_order_by_id(session, order_id) is None:
                raise NotFoundError(f"订单不存在: {order_id}")
            rows = repo.get_order_items_by_order(session, order_id)
            return [_order_item_to_dict(r) for r in rows]
    except SQLAlchemyError as exc:
        raise DatabaseError(f"数据库异常: {exc}") from exc


def get_user_orders(user_id: str) -> list[dict]:
    try:
        with db.session_scope() as session:
            if repo.get_user_by_id(session, user_id) is None:
                raise NotFoundError(f"用户不存在: {user_id}")
            rows = repo.get_orders_by_user(session, user_id)
            return [_order_to_dict(r) for r in rows]
    except SQLAlchemyError as exc:
        raise DatabaseError(f"数据库异常: {exc}") from exc


def search_products(keyword: str) -> list[dict]:
    try:
        with db.session_scope() as session:
            rows = repo.search_products(session, keyword)
            return [_product_to_dict(r) for r in rows]
    except SQLAlchemyError as exc:
        raise DatabaseError(f"数据库异常: {exc}") from exc
