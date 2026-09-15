"""数据访问层（Repository）。

仅负责与 MySQL 交互，不含业务逻辑与参数校验。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db


def get_product_by_id(session: Session, product_id: str) -> db.Product | None:
    return session.get(db.Product, product_id)


def get_user_by_id(session: Session, user_id: str) -> db.User | None:
    return session.get(db.User, user_id)


def get_order_by_id(session: Session, order_id: str) -> db.Order | None:
    return session.get(db.Order, order_id)


def get_inventory_by_product(session: Session, product_id: str) -> list[db.Inventory]:
    stmt = select(db.Inventory).where(db.Inventory.product_id == product_id)
    return list(session.scalars(stmt))


def get_order_items_by_order(session: Session, order_id: str) -> list[db.OrderItem]:
    stmt = select(db.OrderItem).where(db.OrderItem.order_id == order_id)
    return list(session.scalars(stmt))


def get_orders_by_user(session: Session, user_id: str) -> list[db.Order]:
    stmt = select(db.Order).where(db.Order.user_id == user_id)
    return list(session.scalars(stmt))


def search_products(session: Session, keyword: str) -> list[db.Product]:
    """按名称或 SKU 模糊搜索商品。"""
    pattern = f"%{keyword}%"
    stmt = select(db.Product).where(
        db.Product.name.like(pattern) | db.Product.sku.like(pattern)
    )
    return list(session.scalars(stmt))
