import requests
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routers import stats

app = FastAPI(title="Baseball Stats API")


# Without these handlers, unhandled errors surface as a bare "Internal
# Server Error" text body, which the frontend can't parse or display. Only
# network/HTTP failures reaching bref/Savant are labeled as upstream (502);
# everything else -- including bugs in the app's own pandas math -- is a
# 500, so a KeyError from a renamed column isn't misreported as the data
# source being down. (A scrape that returns an unexpected page can still
# fail as a 500 from the parsing code; the detail message names it.)
@app.exception_handler(requests.RequestException)
async def upstream_exception(request: Request, exc: requests.RequestException):
    return JSONResponse(
        status_code=502,
        content={"detail": f"Upstream data fetch failed: {type(exc).__name__}: {exc}"},
    )


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Server error: {type(exc).__name__}: {exc}"},
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
