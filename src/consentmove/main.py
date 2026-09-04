"""ConsentMove — thin FastAPI entrypoint.

Layered package layout:
  src/consentmove/
    core/        # config + db
    schemas/     # pydantic v2 domain models
    api/         # HTTP routers
    services/    # async aiosqlite logic

This module is intentionally thin — it only wires middleware and a /health
check. Routers land in G3; services land in G4.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe used by the deploy pipeline and by curl."""
    return {"status": "ok"}
