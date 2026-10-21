"""RAG（检索增强生成）全链路：文档切块 → 嵌入入库 → 向量检索。

入库（ingest_documents）：markdown 政策文档按「标题分节 → 段落贪心打包 → 超长滑动窗口」
切成 ~500 字的 chunk，嵌入成 1024 维向量存 pgvector；重灌幂等（先删后插+stale 清理）。

检索（search_knowledge）：问题嵌入 → 余弦距离排序取 top-4 → 距离超阈值 0.5 的丢弃。
核心思想：宁可不给也不给错的——检索失败时模型会走「知识库暂无依据」的拒答路径。
"""

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import LLMError
from app.models import KnowledgeChunk

# 分块参数：500 字约一屏政策条款；50 字重叠防止条款在边界被切断。
# 调优方法论：建评测集标注「问题→正确 chunk」映射，网格搜索这些参数看召回率。
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K = 4
# 余弦距离阈值：超过即认为不相关，直接丢弃。把「检索不到」变成「明确不给依据」。
MAX_COSINE_DISTANCE = 0.5
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """按 markdown 结构切块：先按标题分节，节内再打包。标题作为每块前缀保留上下文。"""
    body = text.replace("\r\n", "\n").strip()
    if not body:
        return []
    chunks: list[str] = []
    for heading, section in _sections(body):
        chunks.extend(_chunk_section(heading, section, size, overlap))
    return chunks


def _sections(body: str) -> list[tuple[str, str]]:
    """按 markdown 标题切节，返回 (标题行, 节正文) 列表。标题前的引言单独成节。"""
    matches = list(_HEADING.finditer(body))
    if not matches:
        return [("", body)]
    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = body[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections.append((match.group(0).strip(), body[start:end].strip()))
    return sections


def _chunk_section(heading: str, section: str, size: int, overlap: int) -> list[str]:
    """节内分块：按空行拆段落，贪心打包到 size 预算；超长段落走滑动窗口。

    贪心打包让同节的小段落合并成一块（语义完整），
    超长段落（如长条款列表）用滑动窗口切，重叠 overlap 字保证上下文连续。
    """
    prefix = f"{heading}\n" if heading else ""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", section) if part.strip()]
    if not paragraphs:
        return [heading] if heading else []
    budget = size - len(prefix)
    if budget < 1:
        budget = 1
    packed: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > budget:
            if current:
                packed.append(current)
                current = ""
            windows = _windows(paragraph, budget, overlap)
            packed.extend(windows[:-1])
            current = windows[-1]  # 最后一个窗口留给后续段落继续合并
            continue
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= budget:
            current = candidate
        else:
            if current:
                packed.append(current)
            current = paragraph
    if current:
        packed.append(current)
    return [f"{prefix}{piece}" for piece in packed]


def _windows(text: str, size: int, overlap: int) -> list[str]:
    """滑动窗口切超长段落：步长 = size - overlap，保证相邻窗口有重叠上下文。"""
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    step = overlap if overlap < size else 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - step
    return chunks


def search_knowledge(db: Session, embedding: list[float], limit: int = TOP_K) -> list[str]:
    """向量检索：余弦距离 top-K，超阈值丢弃。

    维度校验在最前面：嵌入模型换版本维度会变，第一时间报错而不是往库里塞错维向量。
    pgvector 的 cosine_distance 越小越相似，按距离升序排列即最相似在前。
    """
    settings = get_settings()
    if len(embedding) != settings.embedding_dim:
        raise LLMError("Embedding dimension does not match configuration")
    distance = KnowledgeChunk.embedding.cosine_distance(embedding)
    rows = db.execute(
        select(KnowledgeChunk, distance.label("distance")).order_by(distance).limit(limit)
    ).all()
    return [row[0].content for row in rows if row[1] < MAX_COSINE_DISTANCE]


def ingest_documents(db: Session, embed, directory: Path) -> int:
    """幂等灌库：目录内文档先删后插；不在目录里的旧文档 chunks 清掉（stale 清理）。"""
    paths = sorted(directory.glob("*.md"))
    names = [path.name for path in paths]
    stale = db.query(KnowledgeChunk)
    if names:
        stale = stale.filter(KnowledgeChunk.document_name.notin_(names))
    stale.delete(synchronize_session=False)
    written = 0
    for path in paths:
        db.query(KnowledgeChunk).filter(KnowledgeChunk.document_name == path.name).delete()
        for piece in chunk_text(path.read_text(encoding="utf-8")):
            vector = embed(piece)
            if len(vector) != get_settings().embedding_dim:
                raise LLMError("Embedding dimension does not match configuration")
            db.add(
                KnowledgeChunk(
                    document_name=path.name,
                    content=piece,
                    embedding=vector,
                )
            )
            written += 1
    db.commit()
    return written
