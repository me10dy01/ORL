"""业务工具（LangChain Tool）。

把已校验的业务工具（Tool → queries → Repository → MySQL）暴露给 LLM 做
Tool Calling。工具参数必须校验；错误以结构化 JSON 返回，不静默吞异常。
"""

from __future__ import annotations

import json

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import tools as biz_tools


def _result_to_text(result: biz_tools.ToolResult) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False)


class ProductIdArgs(BaseModel):
    product_id: str = Field(description="商品ID，例如 P001")


class OrderIdArgs(BaseModel):
    order_id: str = Field(description="订单号，例如 ORD20260001")


class UserIdArgs(BaseModel):
    user_id: str = Field(description="用户ID，例如 U0001")


class SearchProductsArgs(BaseModel):
    keyword: str = Field(description="商品名称或 SKU 关键词，例如 K87")


@tool(args_schema=ProductIdArgs)
def get_product_info(product_id: str) -> str:
    """查询单个商品的详细信息（名称、SKU、分类、价格、描述）。"""
    return _result_to_text(biz_tools.get_product_info(product_id))


@tool(args_schema=ProductIdArgs)
def get_inventory(product_id: str) -> str:
    """查询商品的库存（仓库与数量）。"""
    return _result_to_text(biz_tools.get_inventory(product_id))


@tool(args_schema=OrderIdArgs)
def get_order(order_id: str) -> str:
    """查询单个订单的信息（状态、金额、所属用户、下单时间）。"""
    return _result_to_text(biz_tools.get_order(order_id))


@tool(args_schema=OrderIdArgs)
def get_order_items(order_id: str) -> str:
    """查询订单明细（包含哪些商品及数量、单价）。"""
    return _result_to_text(biz_tools.get_order_items(order_id))


@tool(args_schema=UserIdArgs)
def get_user_orders(user_id: str) -> str:
    """查询某用户名下的全部订单。"""
    return _result_to_text(biz_tools.get_user_orders(user_id))


@tool(args_schema=SearchProductsArgs)
def search_products(keyword: str) -> str:
    """按名称或 SKU 搜索商品，返回匹配的商品列表（含商品ID、名称、价格）。"""
    return _result_to_text(biz_tools.search_products(keyword))


BUSINESS_TOOLS = [
    get_product_info,
    get_inventory,
    get_order,
    get_order_items,
    get_user_orders,
    search_products,
]

TOOL_BY_NAME = {t.name: t for t in BUSINESS_TOOLS}
