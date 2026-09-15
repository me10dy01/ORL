"""数据库初始化脚本：建库、建表、灌入种子数据。

用法：
    uv run python -m app.scripts.init_db

幂等：仅当 products 表为空时才灌入种子数据，不会重复灌入。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import db, seed
from app.config import get_settings


def create_database_if_not_exists() -> None:
    """创建数据库（若不存在）。"""
    settings = get_settings()
    server_engine = create_engine(settings.mysql_server_url())
    try:
        with server_engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_db}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        server_engine.dispose()


def create_tables() -> None:
    """创建全部表。"""
    db.Base.metadata.create_all(db._get_engine())


def seed_if_empty() -> None:
    """表为空时灌入种子数据。"""
    with db.session_scope() as session:
        count = session.scalar(select(func.count()).select_from(db.Product)) or 0
        if count > 0:
            return
        _seed(session)


def _seed(session: Session) -> None:
    # 按实体分组显式 flush，保证外键依赖顺序（父表先落库）
    for p in seed.load_products():
        session.add(
            db.Product(
                product_id=p.product_id,
                sku=p.sku,
                name=p.name,
                category=p.category,
                price=p.price,
                description=p.description,
            )
        )
    session.flush()

    for u in seed.load_users():
        session.add(
            db.User(
                user_id=u.user_id,
                name=u.name,
                phone=u.phone,
                email=u.email,
                level=u.level,
                created_at=date.fromisoformat(u.created_at),
            )
        )
    session.flush()

    for inv in seed.load_inventory():
        session.add(
            db.Inventory(
                product_id=inv.product_id,
                warehouse=inv.warehouse,
                quantity=inv.quantity,
            )
        )
    session.flush()

    for o in seed.load_orders():
        session.add(
            db.Order(
                order_id=o.order_id,
                user_id=o.user_id,
                status=o.status,
                total_amount=o.total_amount,
                created_at=date.fromisoformat(o.created_at),
            )
        )
    session.flush()

    for item in seed.load_order_items():
        session.add(
            db.OrderItem(
                order_item_id=item.order_item_id,
                order_id=item.order_id,
                product_id=item.product_id,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
        )
    session.flush()


def ensure_initialized() -> None:
    """完整初始化：建库 → 建表 → 灌数据。"""
    create_database_if_not_exists()
    create_tables()
    seed_if_empty()


if __name__ == "__main__":
    settings = get_settings()
    if not settings.mysql_password:
        print(
            "提示: .env 中 MYSQL_PASSWORD 为空。若 MySQL root 需要密码，"
            "请在 .env 中设置 MYSQL_PASSWORD=<你的密码> 后重试。"
        )
    try:
        ensure_initialized()
    except SQLAlchemyError as exc:
        print("数据库初始化失败:", exc)
        print(
            "请检查 .env 中的 MySQL 配置："
            "MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB。"
        )
        raise SystemExit(1)
    print("数据库初始化完成")
