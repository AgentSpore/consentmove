"""ConsentMove — FastAPI entrypoint (G3: routers wired in)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.job import router as job_router
from .core.db import init_db

app = FastAPI(
    title="ConsentMove",
    version="0.1.0",
    description=(
        "Migrate user data between cloud accounts under a delegated, "
        "time-boxed OAuth grant — no standing admin permission required."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(job_router, prefix="/api")


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
