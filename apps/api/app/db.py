from collections.abc import Generator

from sqlalchemy import create_engine
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


def get_session_maker():
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    db = get_session_maker()()
    try:
        yield db
    finally:
        db.close()
