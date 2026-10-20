from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db import (
    Base,
    ensure_order_identity_columns,
    ensure_ticket_created_at,
    ensure_ticket_suggested_reply,
    ensure_user_profiles,
    ensure_vector,
    get_engine,
    get_session_maker,
)
from app.models import seed_catalog, seed_users


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = get_engine()
    ensure_vector(engine)
    Base.metadata.create_all(bind=engine)
    ensure_ticket_created_at(engine)
    ensure_ticket_suggested_reply(engine)
    ensure_order_identity_columns(engine)
    ensure_user_profiles(engine)
    db = get_session_maker()()
    try:
        seed_users(db)
        seed_catalog(db)
    finally:
        db.close()
    yield


app = FastAPI(title="ORL Ticket CS", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
