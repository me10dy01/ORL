"""Phase 09 验收测试：Query Rewrite 结合历史正确改写。"""

from __future__ import annotations

import pytest

from app.agents.query_rewrite import rewrite_query

pytestmark = pytest.mark.slow


def _any_keyword(text: str, keywords: list[str]) -> bool:
    """文本包含任一关键词即通过（兼容 LLM 措辞变化）。"""
    return any(kw in text for kw in keywords)


def test_rewrite_no_history_returns_original() -> None:
    assert rewrite_query([], "商城退货政策是什么？") == "商城退货政策是什么？"


def test_rewrite_resolves_pronoun() -> None:
    history = [
        {"role": "user", "content": "K87 支持退货吗？"},
        {"role": "assistant", "content": "支持，签收后 7 天内可无理由退货。"},
    ]
    rewritten = rewrite_query(history, "那运费谁承担？")
    assert _any_keyword(rewritten, ["K87", "k87", "键盘"])
    assert _any_keyword(rewritten, ["运费", "快递费", "邮费"])


def test_rewrite_resolves_second_option() -> None:
    history = [
        {"role": "user", "content": "你们商城支持哪些退货方式？"},
        {"role": "assistant", "content": "支持两种：1. 七天无理由退货；2. 质量问题退货。"},
    ]
    rewritten = rewrite_query(history, "那第二种呢？")
    assert _any_keyword(rewritten, ["质量问题", "质量", "退货"])
