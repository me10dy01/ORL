from fastapi import FastAPI

app = FastAPI(title="ORL Ticket CS")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
