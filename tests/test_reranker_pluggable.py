"""Reranker 可插拔接口测试（模型职责重构 Phase D）。

离线运行：ONNX 打分用替身，不下载模型、不调用 LLM。
覆盖：接口契约、三种实现、降级包装、工厂分发、编号校验。
"""

from __future__ import annotations

import importlib

import pytest

from app.rag import reranker as rr

OrR = rr.OnnxCrossEncoderReranker


def _docs(*contents: str) -> list[dict]:
    return [{"content": c, "metadata": {"chunk_id": f"c{i}"}} for i, c in enumerate(contents)]


# ---- 接口契约 ----


@pytest.mark.parametrize(
    "impl",
    [rr.OnnxCrossEncoderReranker, rr.LLMReranker, rr.NoopReranker],
)
def test_implementations_satisfy_protocol(impl):
    """三种实现都必须满足 Reranker 协议。"""
    assert isinstance(impl(), rr.Reranker)


def test_get_reranker_dispatch():
    """none → 透传；llm/cross_encoder → 均套失败兜底包装。"""
    assert isinstance(rr.get_reranker("none"), rr.NoopReranker)
    for impl in ("llm", "cross_encoder"):
        wrapped = rr.get_reranker(impl)
        assert isinstance(wrapped, rr._FallbackReranker)


# ---- NoopReranker ----


def test_noop_passthrough():
    docs = _docs("a", "b", "c")
    assert rr.NoopReranker().rerank("q", docs, 2) == docs[:2]


# ---- OnnxCrossEncoderReranker（打分为替身） ----


def test_onnx_rerank_orders_by_score(monkeypatch):
    """按分数降序排列；分数相同时保持原召回顺序（稳定）。"""
    docs = _docs("d0", "d1", "d2", "d3")
    scores = [0.1, 0.9, 0.9, 0.5]
    monkeypatch.setattr(OrR, "score", lambda self, q, d: scores)
    out = OrR().rerank("q", docs, 3)
    # d1(0.9) 与 d2(0.9) 同分 → d1 在前；随后 d3(0.5)
    assert [d["content"] for d in out] == ["d1", "d2", "d3"]


def test_onnx_rerank_short_circuit(monkeypatch):
    """候选数 ≤ top_n 时不加载模型、不打分（省资源）。"""
    called: list = []
    monkeypatch.setattr(OrR, "score", lambda self, q, d: called.append(1) or [])
    docs = _docs("a", "b")
    out = OrR().rerank("q", docs, 3)
    assert out == docs
    assert called == []


def test_onnx_lazy_load_not_triggered_on_import():
    """构造重排器不应触发模型下载（懒加载）。"""
    inst = OrR()
    assert inst._session is None and inst._tokenizer is None


# ---- 降级包装 ----


class _Boom:
    def rerank(self, query, documents, top_n):
        raise RuntimeError("model unavailable")


def test_fallback_wrapper_degrades_on_failure():
    """cross-encoder 失败 → 自动降级到兜底实现，不抛异常。"""

    class _FallbackStub:
        def rerank(self, query, documents, top_n):
            return documents[:top_n][::-1]  # 可辨识的兜底输出

    wrapped = rr._FallbackReranker(_Boom(), _FallbackStub())
    docs = _docs("a", "b", "c")
    assert wrapped.rerank("q", docs, 2) == [docs[1], docs[0]]


def test_fallback_wrapper_safe_exit_when_both_fail():
    """主实现与兜底实现都失败 → 退化为向量召回顺序，绝不抛异常。

    背景：LLM 兜底自身也会失败（Qwen3 思考 token 吃满 num_predict 时结构化输出为空）。
    """

    class _AlsoBoom:
        def rerank(self, query, documents, top_n):
            raise RuntimeError("llm fallback also failed")

    wrapped = rr._FallbackReranker(_Boom(), _AlsoBoom())
    docs = _docs("a", "b", "c", "d")
    assert wrapped.rerank("q", docs, 2) == docs[:2]


def test_fallback_wrapper_uses_primary_when_ok():
    class _Primary:
        def rerank(self, query, documents, top_n):
            return documents[:top_n]

    class _NeverUsed:
        def rerank(self, query, documents, top_n):  # pragma: no cover
            raise AssertionError("兜底实现不应被调用")

    docs = _docs("a", "b", "c")
    wrapped = rr._FallbackReranker(_Primary(), _NeverUsed())
    assert wrapped.rerank("q", docs, 2) == docs[:2]


# ---- 兼容入口与编号校验 ----


def test_rerank_compat_entrypoint(monkeypatch):
    """旧签名 rerank() 仍可用（评估代码零改动）。"""
    rk = importlib.import_module("app.rag.reranker")
    monkeypatch.setattr(rk, "get_reranker", lambda impl=None: rr.NoopReranker())
    docs = _docs("a", "b", "c")
    assert rk.rerank("q", docs, 2) == docs[:2]
    assert rk.rerank("q", [], 2) == []


def test_validate_order_filters_and_pads():
    # 越界/重复过滤 + 缺失补齐
    assert rr._validate_order([2, 0, 0, 99], 3) == [2, 0, 1]
    assert rr._validate_order([1], 3) == [1, 0, 2]
