"""回答缓存测试（精确匹配 / 归一化 / 统计 / 清空）。"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from app import cache
from app.cache import normalize

pytestmark = pytest.mark.usefixtures("initialized_db")


def _q() -> str:
    return f"缓存测试问题-{uuid.uuid4().hex[:8]}"


def test_cache_miss_then_hit() -> None:
    question = _q()
    assert cache.get(question) is None

    cache.put(
        question,
        {"route": "rag", "final_answer": "答案", "citations": [], "qa_result": {}},
    )
    got = cache.get(question)
    assert got is not None
    assert got["final_answer"] == "答案"


def test_cache_normalizes_whitespace_and_trailing_punctuation() -> None:
    question = _q()
    cache.put(question, {"final_answer": "归一化"})
    # 前后空白 + 结尾标点应命中同一条
    assert cache.get(f"  {question}？  ") is not None


def test_cache_miss_on_different_question() -> None:
    cache.put(_q(), {"final_answer": "x"})
    assert cache.get(_q()) is None


def test_cache_key_includes_model_name() -> None:
    question = _q()
    own_key = cache.make_key(question)
    other_key = hashlib.sha256(
        f"other-model|{normalize(question)}".encode()
    ).hexdigest()[:32]
    assert own_key != other_key


def test_cache_stats_types_and_clear() -> None:
    q1, q2 = _q(), _q()
    cache.put(q1, {"final_answer": "a"})
    cache.put(q2, {"final_answer": "b"})
    cache.get(q1)

    stats = cache.stats()
    assert stats["entries"] >= 2
    assert stats["hits"] >= 1
    assert isinstance(stats["hits"], int)  # 不是 Decimal

    assert cache.clear() >= 2
    assert cache.get(q1) is None
