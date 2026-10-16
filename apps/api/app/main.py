from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.db import Base, get_engine, get_session_maker
from app.models import seed_users


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    db = get_session_maker()()
    try:
        seed_users(db)
    finally:
        db.close()
    yield


app = FastAPI(title="ORL Ticket CS", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
