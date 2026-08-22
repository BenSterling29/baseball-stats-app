from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import stats

app = FastAPI(title="Baseball Stats API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stats.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
