"""LLM-as-Reranker 回归测试。

项目不再提供 ONNX 或 no-op reranker；调用方无论传入何种旧配置，均应使用
唯一的 LLM 实现，避免运行时悄然回退到效果较差的模型。
"""

from __future__ import annotations

from app.rag import reranker as rr


def _docs(*contents: str) -> list[dict]:
    return [
        {"content": content, "metadata": {"chunk_id": f"c{i}"}}
        for i, content in enumerate(contents)
    ]


def test_only_llm_reranker_is_exposed() -> None:
    """唯一公开实现是 LLMReranker，旧模型类不得重新进入代码路径。"""
    assert isinstance(rr.get_reranker(), rr.LLMReranker)
    assert isinstance(rr.get_reranker("llm"), rr.LLMReranker)
    assert "OnnxCrossEncoderReranker" not in rr.__all__
    assert "NoopReranker" not in rr.__all__


def test_legacy_reranker_name_still_resolves_to_llm(caplog) -> None:
    """旧配置不导致服务失败，但必须被显式忽略。"""
    assert isinstance(rr.get_reranker("cross_encoder"), rr.LLMReranker)
    assert "忽略已移除的 reranker 配置" in caplog.text


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
