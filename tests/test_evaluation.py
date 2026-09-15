"""app/evaluation.py 单元测试（纯离线，不调用 LLM）。"""

from __future__ import annotations

from app.evaluation import _keyword_hit, _p95, _rate, _rate_or_none


class TestKeywordHit:
    def test_empty_answer(self):
        assert _keyword_hit("", ["k"]) is False

    def test_no_keywords_passes_on_nonempty(self):
        assert _keyword_hit("some text", []) is True

    def test_empty_answer_no_keywords(self):
        assert _keyword_hit("", []) is False

    def test_hit(self):
        assert _keyword_hit("退货政策说明", ["退货", "发货"]) is True

    def test_miss(self):
        assert _keyword_hit("发货规则", ["退货", "保修"]) is False


class TestP95:
    def test_empty(self):
        assert _p95([]) == 0.0

    def test_single(self):
        assert _p95([5.0]) == 5.0

    def test_two_values(self):
        assert _p95([1.0, 2.0]) == 2.0

    def test_many_values(self):
        values = list(range(1, 101))  # 1..100
        assert _p95(values) == 95


class TestRate:
    def test_empty(self):
        assert _rate([], "ok") == 1.0

    def test_all_pass(self):
        items = [{"ok": True}, {"ok": True}]
        assert _rate(items, "ok") == 1.0

    def test_half_pass(self):
        items = [{"ok": True}, {"ok": False}]
        assert _rate(items, "ok") == 0.5

    def test_none_pass(self):
        items = [{"ok": False}, {"ok": False}]
        assert _rate(items, "ok") == 0.0


class TestRateOrNull:
    def test_empty_returns_none(self):
        assert _rate_or_none([], "ok") is None

    def test_all_pass(self):
        items = [{"ok": True}, {"ok": True}]
        assert _rate_or_none(items, "ok") == 1.0

    def test_half_pass(self):
        items = [{"ok": True}, {"ok": False}]
        assert _rate_or_none(items, "ok") == 0.5
