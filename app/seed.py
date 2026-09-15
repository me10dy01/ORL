"""星辰数码商城业务种子数据定义与加载。

Phase 02：建立统一虚拟业务世界。
数据文件位于 data/seed/，本模块负责加载并通过 Pydantic 做类型校验，
供后续 Phase（MySQL / RAG / Tool）复用，保证全项目数据来源唯一。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEED_DIR = DATA_DIR / "seed"
DOCUMENTS_DIR = DATA_DIR / "documents"
EVAL_DIR = DATA_DIR / "eval"


class Product(BaseModel):
    """商品。"""

    product_id: str
    sku: str
    name: str
    category: str
    price: Decimal = Field(ge=0)
    description: str


class User(BaseModel):
    """用户。"""

    user_id: str
    name: str
    phone: str
    email: str
    level: str
    created_at: str


class Inventory(BaseModel):
    """库存。"""

    product_id: str
    warehouse: str
    quantity: int = Field(ge=0)


class Order(BaseModel):
    """订单。"""

    order_id: str
    user_id: str
    status: str
    total_amount: Decimal = Field(ge=0)
    created_at: str


class OrderItem(BaseModel):
    """订单明细。"""

    order_item_id: str
    order_id: str
    product_id: str
    quantity: int = Field(gt=0)
    unit_price: Decimal = Field(ge=0)


class TestQuestion(BaseModel):
    """测试问题。"""

    id: str
    category: str
    question: str
    history: list[str] = Field(default_factory=list)
    expected_route: str
    expected_entities: list[str] = Field(default_factory=list)
    ground_truth: str
    answer_keywords: list[str] = Field(default_factory=list)
    notes: str = ""


class TestDataset(BaseModel):
    """测试问题集。"""

    dataset_name: str
    version: int
    description: str = ""
    questions: list[TestQuestion] = Field(default_factory=list)


def _load_json(path: Path) -> object:
    """读取 JSON 文件。"""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_json_list(path: Path) -> list[dict]:
    """读取 JSON 文件并返回 dict 列表（种子数据为数组结构）。"""
    data = _load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"种子数据文件应为 JSON 数组: {path}")
    return data


def load_products() -> list[Product]:
    return [Product.model_validate(item) for item in _load_json_list(SEED_DIR / "products.json")]


def load_users() -> list[User]:
    return [User.model_validate(item) for item in _load_json_list(SEED_DIR / "users.json")]


def load_inventory() -> list[Inventory]:
    return [Inventory.model_validate(item) for item in _load_json_list(SEED_DIR / "inventory.json")]


def load_orders() -> list[Order]:
    return [Order.model_validate(item) for item in _load_json_list(SEED_DIR / "orders.json")]


def load_order_items() -> list[OrderItem]:
    return [OrderItem.model_validate(item) for item in _load_json_list(SEED_DIR / "order_items.json")]


def load_test_questions() -> list[TestQuestion]:
    """加载测试问题集（data/eval/test_questions.json）。"""
    dataset = TestDataset.model_validate(_load_json(EVAL_DIR / "test_questions.json"))
    return dataset.questions
