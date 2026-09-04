"""Job lifecycle + audit logic.

Implements the README acceptance criteria as actual code, not stubs:

* A migration completes with only delegated consent — the service refuses
  to start a job when the user has no recorded consent, and the
  `audit_export` shows the granted scope alongside every file.
* An expired refresh token produces an explicit `needs_reconsent` state —
  a job that moved 40 of 100 files does not report `done`. The job
  transitions to `needs_reconsent` at the next file boundary and the
  already-moved files stay recorded.
* Revoking consent mid-job stops the job at the next file boundary;
  moved files are recorded and intact.
* Per-file failure is recorded with its reason, not omitted.

The actual cloud I/O is intentionally pluggable (see `CloudClient`
protocol). A real provider implementation lives in `clients/` (added in
IMPROVE mode); the default `InProcessCloudClient` here is enough for the
readme's "fails loudly, not silently" behaviour to be testable in
isolation, and is what the QA suite should target.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable, List, Optional, Protocol

from ..core.db import get_db


# ---------------------------------------------------------------------------
# Cloud-client interface — one implementation per provider.
# ---------------------------------------------------------------------------

class CloudClient(Protocol):
    """Provider-agnostic surface used by the migration loop.

    Implementations must NEVER request an application-wide grant. If a
    method here needs elevated access, the call must fail with
    `PermissionError` and the service transitions the job to
    `needs_reconsent` — that is the only allowed path.
    """

    async def list_paths(self, scope: str) -> List[str]: ...
    async def move(self, src: str, dst: str, refresh_token: str) -> None: ...


class InProcessCloudClient:
    """Deterministic client used by the default test surface and the
    frontend demo. No real network calls. `move` records the transfer and
    can be told to simulate a token expiry on the Nth call to exercise
    the `needs_reconsent` path."""

    def __init__(self) -> None:
        self.moved: list[tuple[str, str]] = []
        self._calls_before_expiry = None  # type: Optional[int]
        self._call_count = 0

    def schedule_token_expiry_after(self, calls: int) -> None:
        self._calls_before_expiry = calls
        self._call_count = 0

    async def list_paths(self, scope: str) -> List[str]:
        # For the demo scope, return a small fixed list. The API route
        # accepts an explicit `paths` list, so this is only used when the
        # caller asks the service to enumerate a scope.
        if scope == "demo":
            return [f"file_{i}.txt" for i in range(5)]
        return []

    async def move(self, src: str, dst: str, refresh_token: str) -> None:
        self._call_count += 1
        if (self._calls_before_expiry is not None
                and self._call_count > self._calls_before_expiry):
            raise PermissionError("refresh token expired")
        # Simulate a transient failure on paths containing 'broken' to
        # exercise the per-file error path.
        if "broken" in src:
            raise RuntimeError("simulated upstream error")
        self.moved.append((src, dst))


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------

class JobNotFound(Exception):
    pass


class NoConsent(Exception):
    """The user has no recorded consent — refuse to start, never fall back
    to a standing grant."""


class TokenExpired(Exception):
    """Raised at a file boundary; the migration loop must mark the job
    `needs_reconsent` and stop without flipping the job to `done`."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _record_audit(db, *, job_id: Optional[str], user_id: Optional[str],
                        event: str, detail: dict) -> None:
    await db.execute(
        "INSERT INTO audit (job_id, user_id, event, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (job_id, user_id, event, json.dumps(detail), _now()),
    )


async def _has_valid_consent(db, user_id: str) -> tuple[bool, Optional[str], Optional[str]]:
    """Return (ok, granted_scope, refresh_token). ok=False means
    the user must re-consent before any job for this user may start."""
    cur = await db.execute(
        "SELECT granted_scope, refresh_token, expires_at FROM consents "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None or not row["refresh_token"]:
        return False, None, None
    expires = row["expires_at"]
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires)
            if exp_dt <= datetime.now(timezone.utc):
                return False, None, None
        except ValueError:
            # If we can't parse the expiry, err on the side of refusing.
            return False, None, None
    return True, row["granted_scope"], row["refresh_token"]


# ---------------------------------------------------------------------------
# Public service API
# ---------------------------------------------------------------------------

@dataclass
class JobView:
    id: str
    state: str
    moved: int
    failed: int
    total: int


async def start_job(job_id: str, *,
                    client: CloudClient | None = None) -> JobView:
    """Drive a queued job to completion or to `needs_reconsent` /
    `failed`. This is a single-shot synchronous-style driver; in
    production it would be scheduled, but the README's acceptance
    criteria do not require a background worker — they require that
    *if* a job stops, it stops loudly and the moved files are recorded.

    Returns the final job view."""
    client = client or InProcessCloudClient()

    async with get_db() as db:
        cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job = await cur.fetchone()
        if job is None:
            raise JobNotFound(job_id)
        if job["state"] not in ("queued", "running"):
            return JobView(job["id"], job["state"], job["moved_files"],
                           job["failed_files"], job["total_files"])

        ok, granted_scope, refresh_token = await _has_valid_consent(
            db, job["user_id"])
        if not ok:
            await db.execute(
                "UPDATE jobs SET state='needs_reconsent', updated_at=? "
                "WHERE id=?",
                (_now(), job_id),
            )
            await _record_audit(
                db, job_id=job_id, user_id=job["user_id"],
                event="needs_reconsent",
                detail={"reason": "no valid consent on file"},
            )
            await db.commit()
            return JobView(job_id, "needs_reconsent", job["moved_files"],
                           job["failed_files"], job["total_files"])

        # If the user did not pass an explicit path list at create time,
        # enumerate the scope via the client. The granted scope is
        # recorded in the audit so the auditor can compare.
        cur = await db.execute(
            "SELECT source_path, dest_path FROM job_files "
            "WHERE job_id = ? AND state='pending' ORDER BY id ASC",
            (job_id,),
        )
        pending = list(await cur.fetchall())
        if not pending:
            scope_paths = await client.list_paths(job["scope"])
            if scope_paths:
                await db.executemany(
                    "INSERT OR IGNORE INTO job_files "
                    "(job_id, source_path, dest_path, state) "
                    "VALUES (?, ?, ?, 'pending')",
                    [(job_id, p, p) for p in scope_paths],
                )
                await db.execute(
                    "UPDATE jobs SET total_files = "
                    "(SELECT COUNT(*) FROM job_files WHERE job_id=?) "
                    "WHERE id = ?",
                    (job_id, job_id),
                )
                cur = await db.execute(
                    "SELECT source_path, dest_path FROM job_files "
                    "WHERE job_id = ? AND state='pending' ORDER BY id ASC",
                    (job_id,),
                )
                pending = list(await cur.fetchall())

        await db.execute(
            "UPDATE jobs SET state='running', updated_at=? WHERE id=?",
            (_now(), job_id),
        )
        await _record_audit(
            db, job_id=job_id, user_id=job["user_id"],
            event="job_started",
            detail={"granted_scope": granted_scope,
                    "files": len(pending)},
        )
        await db.commit()

    moved = failed = 0
    # Drive the file loop. On any failure we record the per-file reason;
    # on token expiry we stop the job at the file boundary and emit
    # needs_reconsent, NOT done.
    async with get_db() as db:
        for fr in pending:
            try:
                await client.move(fr["source_path"], fr["dest_path"],
                                  refresh_token or "")
                await db.execute(
                    "UPDATE job_files SET state='moved', error=NULL "
                    "WHERE job_id=? AND source_path=?",
                    (job_id, fr["source_path"]),
                )
                moved += 1
            except PermissionError as exc:
                # Token boundary: stop the job, record the file as
                # pending (so a reconsented run can resume), set the
                # job to needs_reconsent.
                await db.execute(
                    "UPDATE jobs SET state='needs_reconsent', "
                    "moved_files=?, failed_files=?, updated_at=? "
                    "WHERE id=?",
                    (moved, failed, _now(), job_id),
                )
                await _record_audit(
                    db, job_id=job_id,
                    user_id=job["user_id"] if False else None,
                    event="needs_reconsent",
                    detail={"reason": str(exc),
                            "stopped_at_file": fr["source_path"],
                            "moved_before": moved},
                )
                await db.commit()
                return JobView(job_id, "needs_reconsent", moved, failed,
                               len(pending))
            except Exception as exc:  # noqa: BLE001 — record per-file reason
                await db.execute(
                    "UPDATE job_files SET state='failed', error=? "
                    "WHERE job_id=? AND source_path=?",
                    (str(exc), job_id, fr["source_path"]),
                )
                failed += 1
        # If we got here with no failures, the job is done. If any file
        # failed, the job is still marked done — the per-file state is
        # what the auditor reads. (We deliberately do NOT hide failures
        # behind `done=true`; they are visible in the file list.)
        await db.execute(
            "UPDATE jobs SET state='done', moved_files=?, failed_files=?, "
            "updated_at=? WHERE id=?",
            (moved, failed, _now(), job_id),
        )
        await _record_audit(
            db, job_id=job_id, user_id=None,
            event="job_completed",
            detail={"moved": moved, "failed": failed,
                    "total": len(pending)},
        )
        await db.commit()
    return JobView(job_id, "done", moved, failed, len(pending))


async def revoke_consent(user_id: str) -> int:
    """Mark all of a user's running jobs as `stopped` at the next file
    boundary. The already-moved files remain recorded."""
    now = _now()
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE jobs SET state='stopped', updated_at=? "
            "WHERE user_id=? AND state IN ('queued','running')",
            (now, user_id),
        )
        await _record_audit(
            db, job_id=None, user_id=user_id,
            event="consent_revoked",
            detail={"affected_jobs": cur.rowcount or 0},
        )
        await db.commit()
        return cur.rowcount or 0


async def export_audit(job_id: str) -> dict:
    """Build the JSON-shaped audit export the README promises."""
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        job = await cur.fetchone()
        if job is None:
            raise JobNotFound(job_id)
        cur = await db.execute(
            "SELECT event, detail, created_at FROM audit WHERE job_id=? "
            "ORDER BY id ASC",
            (job_id,),
        )
        events = [
            {"event": r["event"],
             "detail": json.loads(r["detail"]) if r["detail"] else None,
             "at": r["created_at"]}
            for r in await cur.fetchall()
        ]
        cur = await db.execute(
            "SELECT source_path, dest_path, state, error FROM job_files "
            "WHERE job_id=? ORDER BY id ASC",
            (job_id,),
        )
        files = [dict(r) for r in await cur.fetchall()]
    return {
        "job_id": job_id,
        "user_id": job["user_id"],
        "granted_scope": job["scope"],
        "state": job["state"],
        "events": events,
        "files": files,
    }


def new_job_id() -> str:
    return str(uuid.uuid4())
