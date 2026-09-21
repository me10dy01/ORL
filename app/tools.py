"""业务工具层（Tool）。

对外暴露明确的业务能力（禁止 execute_sql 之类的通用工具），
负责参数验证与异常兜底，禁止直接操作数据库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app import queries
from app.exceptions import AppError, DatabaseError, NotFoundError, ValidationError

PRODUCT_ID_RE = re.compile(r"^P\d{3}$")
ORDER_ID_RE = re.compile(r"^ORD\d{8}$")
USER_ID_RE = re.compile(r"^U\d{4}$")


@dataclass
class ToolResult:
    """工具统一返回结构。"""

    ok: bool
    data: Any = None
    error: str | None = None
    error_type: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "error_type": self.error_type,
        }


def _validate(value: str, pattern: re.Pattern[str], name: str) -> None:
    """校验参数非空且符合格式（区分参数缺失 / 参数非法）。"""
    if not value or not isinstance(value, str):
        raise ValidationError(f"参数缺失: {name} 不能为空")
    if not pattern.fullmatch(value):
        raise ValidationError(f"参数非法: {name}={value}")


def _run(fn: Callable[[], Any]) -> ToolResult:
    """统一异常兜底，转换为结构化 ToolResult。"""
    try:
        return ToolResult(ok=True, data=fn())
    except ValidationError as exc:
        return ToolResult(ok=False, error=str(exc), error_type="validation_error")
    except NotFoundError as exc:
        return ToolResult(ok=False, error=str(exc), error_type="not_found")
    except DatabaseError as exc:
        return ToolResult(ok=False, error=str(exc), error_type="database_error")
    except AppError as exc:
        return ToolResult(ok=False, error=str(exc), error_type="app_error")


def get_product_info(product_id: str) -> ToolResult:
    """查询商品信息。"""

    def _fn() -> dict:
        _validate(product_id, PRODUCT_ID_RE, "product_id")
        return queries.get_product_info(product_id)

    return _run(_fn)


def get_inventory(product_id: str) -> ToolResult:
    """查询商品库存。"""

    def _fn() -> list[dict]:
        _validate(product_id, PRODUCT_ID_RE, "product_id")
        return queries.get_inventory(product_id)

    return _run(_fn)


def get_order(order_id: str) -> ToolResult:
    """查询订单信息。"""

    def _fn() -> dict:
        _validate(order_id, ORDER_ID_RE, "order_id")
        return queries.get_order(order_id)

    return _run(_fn)


def get_order_items(order_id: str) -> ToolResult:
    """查询订单明细。"""

    def _fn() -> list[dict]:
        _validate(order_id, ORDER_ID_RE, "order_id")
        return queries.get_order_items(order_id)

    return _run(_fn)


def get_user_orders(user_id: str) -> ToolResult:
    """查询用户全部订单。"""

    def _fn() -> list[dict]:
        _validate(user_id, USER_ID_RE, "user_id")
        return queries.get_user_orders(user_id)

    return _run(_fn)


def search_products(keyword: str) -> ToolResult:
    """按名称或 SKU 搜索商品。"""

    def _fn() -> list[dict]:
        if not keyword or not isinstance(keyword, str):
            raise ValidationError("参数缺失: keyword 不能为空")
        return queries.search_products(keyword.strip())

    return _run(_fn)
