"""知识库文档加载器（多格式，Phase 扩展）。

支持：.md / .markdown / .txt / .pdf / .docx
返回 [(页码, 文本), ...]：PDF 按页返回，其余为单页 (1, 全文)。
"""

from __future__ import annotations

from pathlib import Path

from app.exceptions import ValidationError

SUPPORTED_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf", ".docx"}

# 用户展示用：合并 .md/.markdown 为一项，避免冗余
SUPPORTED_HINT = "Markdown (.md)、.txt、.pdf、.docx"


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def extract_pages(path: Path) -> list[tuple[int, str]]:
    """按扩展名提取文本，返回 (页码, 文本) 列表。"""
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        return [(1, _read_text(path))]
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix == ".docx":
        return [(1, _read_docx(path))]
    raise ValidationError(f"不支持的文件格式: {suffix}（支持 {SUPPORTED_HINT}）")


def _read_text(path: Path) -> str:
    """纯文本读取，utf-8 优先，兼容 gbk。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    non_printable = sum(1 for c in text if c != "\n" and c != "\r" and not c.isprintable())
    if text and non_printable / len(text) > 0.5:
        raise ValidationError(
            f"文件内容异常（可能是二进制文件伪装为文本）: {path.name}"
        )
    return text


def _read_pdf(path: Path) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ValidationError(f"PDF 解析失败: {path.name} ({exc})") from exc
    return [
        (i, (page.extract_text() or ""))
        for i, page in enumerate(reader.pages, start=1)
    ]


def _read_docx(path: Path) -> str:
    import docx

    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise ValidationError(f"DOCX 解析失败: {path.name} ({exc})") from exc
    return "\n".join(p.text for p in document.paragraphs)
