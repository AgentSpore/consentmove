"""aiosqlite plumbing for ConsentMove.

Schema captures the README's "audit record: who consented, when, what scope,
which files moved" requirement, plus the per-file progress state needed to
resume a partially-completed job.

  jobs        — one row per migration, owned by the consenting user.
  consents    — append-only; one row per granted consent (the latest wins).
  job_files   — per-file progress; failure reasons recorded, not omitted.
  audit       — append-only event log; exportable as JSON for the auditor.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from .config import settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    source          TEXT NOT NULL,
    destination     TEXT NOT NULL,
    scope           TEXT NOT NULL,
    state           TEXT NOT NULL,        -- queued | running | needs_reconsent | done | failed | stopped
    total_files     INTEGER NOT NULL DEFAULT 0,
    moved_files     INTEGER NOT NULL DEFAULT 0,
    failed_files    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_jobs_user ON jobs(user_id);
CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state);

CREATE TABLE IF NOT EXISTS consents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    granted_scope   TEXT NOT NULL,
    granted_at      TEXT NOT NULL,
    refresh_token   TEXT,
    expires_at      TEXT
);

CREATE INDEX IF NOT EXISTS ix_consents_user ON consents(user_id);

CREATE TABLE IF NOT EXISTS job_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT NOT NULL,
    source_path     TEXT NOT NULL,
    dest_path       TEXT NOT NULL,
    state           TEXT NOT NULL,        -- pending | moved | failed
    error           TEXT,
    UNIQUE(job_id, source_path)
);

CREATE INDEX IF NOT EXISTS ix_job_files_job ON job_files(job_id);

CREATE TABLE IF NOT EXISTS audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          TEXT,
    user_id         TEXT,
    event           TEXT NOT NULL,
    detail          TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_audit_job ON audit(job_id);
"""


@asynccontextmanager
async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """Yield an aiosqlite connection. Caller is responsible for transactions."""
    db = await aiosqlite.connect(settings().database_url)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA foreign_keys = ON")
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    async with get_db() as db:
        await db.executescript(SCHEMA)
        await db.commit()
