"""LLM-as-Reranker 回归测试。"""

from __future__ import annotations

from app.rag import reranker as rr


def _docs(*contents: str) -> list[dict]:
    return [
        {"content": content, "metadata": {"chunk_id": f"c{i}"}}
        for i, content in enumerate(contents)
    ]


def test_only_llm_reranker_is_exposed() -> None:
    """唯一公开实现是 LLMReranker。"""
    assert isinstance(rr.get_reranker(), rr.LLMReranker)
    assert "OnnxCrossEncoderReranker" not in rr.__all__
    assert "NoopReranker" not in rr.__all__
    assert "rerank" not in rr.__all__


def test_llm_reranker_short_circuits_when_no_sorting_is_needed(monkeypatch) -> None:
    """候选数不超过 top_n 时不调用 LLM。"""
    docs = _docs("a", "b")
    monkeypatch.setattr(
        rr, "get_structured_model", lambda: (_ for _ in ()).throw(AssertionError())
    )
    assert rr.LLMReranker().rerank("q", docs, 3) == docs


def test_llm_failure_keeps_vector_retrieval_order(monkeypatch) -> None:
    """模型不可用时不切换模型，只安全退化为原候选顺序。"""
    docs = _docs("a", "b", "c")
    monkeypatch.setattr(
        rr, "get_structured_model", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    assert rr.LLMReranker().rerank("q", docs, 2) == docs[:2]


def test_validate_order_filters_duplicates_and_pads() -> None:
    assert rr._validate_order([2, 0, 0, 99], 3) == [2, 0, 1]
    assert rr._validate_order([1], 3) == [1, 0, 2]
