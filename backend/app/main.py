from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import stats

app = FastAPI(title="Baseball Stats API")


# Most runtime failures here are upstream scrape errors (bref/Savant down,
# a season with no data yet, a changed page layout). Without this handler
# they surface as a bare "Internal Server Error" text body, which the
# frontend can't parse or display usefully.
@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    return JSONResponse(
        status_code=502,
        content={"detail": f"Upstream data fetch failed: {type(exc).__name__}: {exc}"},
    )

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
