import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm import LLMError
from app.models import KnowledgeChunk

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
TOP_K = 4
MAX_COSINE_DISTANCE = 0.5
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    body = text.replace("\r\n", "\n").strip()
    if not body:
        return []
    chunks: list[str] = []
    for heading, section in _sections(body):
        chunks.extend(_chunk_section(heading, section, size, overlap))
    return chunks


def _sections(body: str) -> list[tuple[str, str]]:
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
            current = windows[-1]
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
    settings = get_settings()
    if len(embedding) != settings.embedding_dim:
        raise LLMError("Embedding dimension does not match configuration")
    distance = KnowledgeChunk.embedding.cosine_distance(embedding)
    rows = db.execute(
        select(KnowledgeChunk, distance.label("distance")).order_by(distance).limit(limit)
    ).all()
    return [row[0].content for row in rows if row[1] < MAX_COSINE_DISTANCE]


def ingest_documents(db: Session, embed, directory: Path) -> int:
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
