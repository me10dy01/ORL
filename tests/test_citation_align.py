"""Phase 15 扩展验收：Citation 逐句对齐（切句 + 逐句归因）。"""

from __future__ import annotations

import pytest

from app.agents.citation_align import align_citations, split_sentences


def test_split_sentences_strips_markdown_noise() -> None:
    text = "## 标题\n- 第一句内容在这里。\n\n第二句内容也在这里！\n|---|---|\n"
    sents = split_sentences(text)
    assert "第一句内容在这里。" in sents
    assert "第二句内容也在这里！" in sents
    # Markdown 标题前缀剥离后过短，被丢弃
    assert "标题" not in sents
    # 表格分隔行被剔除
    assert not any(set(s) <= set("|-: ") for s in sents)


def test_split_sentences_drops_short_fragment() -> None:
    assert split_sentences("好。") == []


@pytest.mark.slow
def test_align_all_supported() -> None:
    ctx = ["星辰数码商城支持签收后 7 天内无理由退货，质量问题 15 天内可退换。"]
    answer = "本商城支持签收后 7 天内无理由退货，质量问题 15 天内可退换。"
    result = align_citations(answer, ctx)
    assert result["unsupported_sentences"] == []
    assert result["supported_ratio"] == 1.0


@pytest.mark.slow
def test_align_detects_unsupported_sentence() -> None:
    ctx = ["星辰数码商城支持签收后 7 天内无理由退货。"]
    answer = "本商城支持签收后 7 天内无理由退货。另外本商城提供免费上门安装服务。"
    result = align_citations(answer, ctx)
    assert result["unsupported_sentences"], "应检出无上下文依据的句子"
    assert result["supported_ratio"] < 1.0
