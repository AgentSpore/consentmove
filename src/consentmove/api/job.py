"""Jobs + consents + audit HTTP router.

The router delegates persistence to `services.job_service` (added in G4) but
keeps the endpoint surface stable so the G4 implementation can drop in
without API churn. Import is wrapped in try/except so the module loads
during scaffolding before the service exists; routes that need the service
return 503 in that window.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from ..core.db import get_db
from ..schemas.job import (
    AuditExport,
    ConsentCreate,
    ConsentRead,
    JobCreate,
    JobFileRead,
    JobRead,
    JobState,
    JobSummary,
)

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreate) -> JobRead:
    job_id = str(uuid.uuid4())
    now = _now()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO jobs
                (id, user_id, source, destination, scope, state,
                 total_files, moved_files, failed_files, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued', 0, 0, 0, ?, ?)
            """,
            (job_id, payload.user_id, payload.source, payload.destination,
             payload.scope, now, now),
        )
        if payload.paths:
            await db.executemany(
                """
                INSERT INTO job_files (job_id, source_path, dest_path, state)
                VALUES (?, ?, ?, 'pending')
                """,
                [(job_id, p, p) for p in payload.paths],
            )
            await db.execute(
                "UPDATE jobs SET total_files = ? WHERE id = ?",
                (len(payload.paths), job_id),
            )
        await db.execute(
            """
            INSERT INTO audit (job_id, user_id, event, detail, created_at)
            VALUES (?, ?, 'job_created', ?, ?)
            """,
            (job_id, payload.user_id,
             json.dumps({"scope": payload.scope, "source": payload.source,
                         "destination": payload.destination}), now),
        )
        await db.commit()
    return await _read_job(job_id)


@router.get("/jobs", response_model=List[JobSummary])
async def list_jobs(user_id: str | None = None) -> List[JobSummary]:
    async with get_db() as db:
        if user_id is not None:
            cur = await db.execute(
                "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            )
        rows = await cur.fetchall()
    return [JobSummary(**dict(r)) for r in rows]


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(job_id: str) -> JobRead:
    job = await _read_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/jobs/{job_id}/audit", response_model=AuditExport)
async def export_audit(job_id: str) -> AuditExport:
    job = await _read_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    async with get_db() as db:
        cur = await db.execute(
            "SELECT event, detail, created_at FROM audit "
            "WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        )
        rows = await cur.fetchall()
    events = [
        {"event": r["event"],
         "detail": json.loads(r["detail"]) if r["detail"] else None,
         "at": r["created_at"]}
        for r in rows
    ]
    return AuditExport(
        job_id=job.id,
        user_id=job.user_id,
        granted_scope=job.scope,
        events=events,
        files=job.files,
    )


@router.post("/consents", response_model=ConsentRead,
             status_code=status.HTTP_201_CREATED)
async def grant_consent(payload: ConsentCreate) -> ConsentRead:
    now = _now()
    async with get_db() as db:
        cur = await db.execute(
            """
            INSERT INTO consents (user_id, granted_scope, granted_at,
                                 refresh_token, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (payload.user_id, payload.granted_scope, now,
             payload.refresh_token,
             payload.expires_at.isoformat() if payload.expires_at else None),
        )
        await db.execute(
            """
            INSERT INTO audit (job_id, user_id, event, detail, created_at)
            VALUES (NULL, ?, 'consent_granted', ?, ?)
            """,
            (payload.user_id,
             json.dumps({"scope": payload.granted_scope}), now),
        )
        await db.commit()
        consent_id = cur.lastrowid
    return ConsentRead(
        id=consent_id,
        user_id=payload.user_id,
        granted_scope=payload.granted_scope,
        granted_at=datetime.fromisoformat(now),
        expires_at=payload.expires_at,
    )


@router.get("/consents/latest", response_model=ConsentRead | None)
async def latest_consent(user_id: str) -> ConsentRead | None:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT * FROM consents WHERE user_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    return ConsentRead(
        id=d["id"],
        user_id=d["user_id"],
        granted_scope=d["granted_scope"],
        granted_at=datetime.fromisoformat(d["granted_at"]),
        expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None,
    )


async def _read_job(job_id: str) -> JobRead | None:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        cur = await db.execute(
            "SELECT * FROM job_files WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        )
        files_rows = await cur.fetchall()
    d = dict(row)
    return JobRead(
        id=d["id"],
        user_id=d["user_id"],
        source=d["source"],
        destination=d["destination"],
        scope=d["scope"],
        state=JobState(d["state"]),
        total_files=d["total_files"],
        moved_files=d["moved_files"],
        failed_files=d["failed_files"],
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        files=[JobFileRead(**dict(fr)) for fr in files_rows],
    )
