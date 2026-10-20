from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None


def get_engine(url: str | None = None):
    global _engine
    if url is not None:
        _engine = create_engine(url)
    elif _engine is None:
        _engine = create_engine(get_settings().database_url)
    return _engine


def reset_engine(url: str | None = None):
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
    if url is not None:
        _engine = create_engine(url)


def ensure_vector(engine=None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def ensure_ticket_created_at(engine=None) -> None:
    """Add tickets.created_at on databases created before that column existed."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            )
        )


def ensure_ticket_suggested_reply(engine=None) -> None:
    """Add tickets.suggested_reply on databases created before agent assist."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS suggested_reply VARCHAR")
        )


def ensure_order_identity_columns(engine=None) -> None:
    """Add identity columns on databases created before phone and name lookup."""
    engine = engine or get_engine()
    statements = [
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS user_id VARCHAR",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS phone VARCHAR",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name VARCHAR",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_id VARCHAR",
        "ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()",
        "ALTER TABLE conversation_slots ADD COLUMN IF NOT EXISTS last_phone VARCHAR",
        "ALTER TABLE conversation_slots ADD COLUMN IF NOT EXISTS last_name VARCHAR",
        "ALTER TABLE conversation_slots ADD COLUMN IF NOT EXISTS candidate_order_ids VARCHAR",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def ensure_user_profiles(engine=None) -> None:
    """Create user_profiles on databases created before profiles existed."""
    engine = engine or get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS user_profiles ("
                "user_id VARCHAR PRIMARY KEY REFERENCES users(id), "
                "name VARCHAR, phone VARCHAR, last_product_id VARCHAR, last_ticket_summary VARCHAR"
                ")"
            )
        )


def get_session_maker():
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    db = get_session_maker()()
    try:
        yield db
    finally:
        db.close()
