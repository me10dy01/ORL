from pathlib import Path

from app.db import Base, ensure_vector, get_engine, get_session_maker
from app.llm import embed
from app.services.rag import ingest_documents


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").is_file() and (parent / "apps").is_dir():
            return parent
    raise FileNotFoundError("repository root not found")


def main() -> None:
    engine = get_engine()
    ensure_vector(engine)
    Base.metadata.create_all(bind=engine)
    db = get_session_maker()()
    try:
        count = ingest_documents(db, embed, repo_root() / "data" / "documents")
    finally:
        db.close()
    print(f"ingested {count} chunks")


if __name__ == "__main__":
    main()
