"""Phase 04 验收测试：测试问题能够检索到预期知识。

依赖本机 Ollama（bge-m3 嵌入 + qwen3:8b 生成）。
索引构建为幂等操作，通过 file_hash 去重。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag import answer, build_index, retrieve
from app.rag.loaders import is_supported
from app.seed import DOCUMENTS_DIR

pytestmark = pytest.mark.slow


RETRIEVAL_CASES = [
    ("星辰数码商城的退货政策是什么？", ["无理由退货"]),
    ("K87 键盘支持退货吗？", ["K87"]),
    ("退货时运费由谁承担？", ["运费"]),
    ("商城下单后多久发货？", ["24 小时"]),
    ("耳机的保修期是多久？", ["2 年"]),
]


def _supported_doc_count() -> int:
    """按目录实际内容计算期望文档数（避免新增文档后断言过期）。"""
    return sum(
        1
        for path in Path(DOCUMENTS_DIR).iterdir()
        if path.is_file() and is_supported(path.name)
    )


def test_build_index_counts(rag_index: dict) -> None:
    expected = _supported_doc_count()
    assert expected > 0, f"{DOCUMENTS_DIR} 下没有受支持的文档"
    assert rag_index["documents"] == expected
    assert rag_index["chunks"] > 0


def test_ingest_is_idempotent(rag_index: dict) -> None:
    again = build_index()
    assert again["ingested_chunks"] == 0


@pytest.mark.parametrize("query,keywords", RETRIEVAL_CASES)
def test_retrieval_hits_expected_knowledge(
    rag_index: dict, query: str, keywords: list[str]
) -> None:
    results = retrieve(query, top_k=5)
    joined = " ".join(r["content"] for r in results)
    assert any(k in joined for k in keywords), (
        f"{query!r} 未检索到预期知识 {keywords}"
    )


def test_retrieval_sources_have_traceable_metadata(rag_index: dict) -> None:
    results = retrieve("K87 键盘支持退货吗？", top_k=3)
    assert results
    for r in results:
        meta = r["metadata"]
        assert meta["document_id"].startswith("doc_")
        assert meta["chunk_id"]
        assert meta["file_name"]
        assert meta["source"]
        assert meta["file_hash"]


def test_answer_returns_text_and_sources(rag_index: dict) -> None:
    result = answer("K87 键盘支持退货吗？", top_k=4)
    assert isinstance(result["answer"], str) and result["answer"]
    assert result["sources"]
