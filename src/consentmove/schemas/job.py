"""Pydantic v2 models for the jobs domain.

The Create/Read pair matches the MVP scope exactly:

  POST /jobs {source, destination, scope}
  GET  /jobs/{id} reports per-file progress and every file that failed,
       with the reason.

JobState is a string Enum, not free text, so a partially-completed job with
an expired refresh token can only land on `needs_reconsent` — never on a
silent `done`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_RECONSENT = "needs_reconsent"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"


class FileState(str, Enum):
    PENDING = "pending"
    MOVED = "moved"
    FAILED = "failed"


class JobCreate(BaseModel):
    """Input for `POST /jobs`. `scope` is the user's chosen subset, recorded
    into the audit log; the OAuth flow negotiates the actual granted scope."""
    user_id: str = Field(..., min_length=1, max_length=128)
    source: str = Field(..., min_length=1)
    destination: str = Field(..., min_length=1)
    scope: str = Field(..., min_length=1)
    paths: Optional[List[str]] = None  # None = whole drive; explicit list = subset


class JobFileRead(BaseModel):
    source_path: str
    dest_path: str
    state: FileState
    error: Optional[str] = None


class JobRead(BaseModel):
    id: str
    user_id: str
    source: str
    destination: str
    scope: str
    state: JobState
    total_files: int
    moved_files: int
    failed_files: int
    created_at: datetime
    updated_at: datetime
    files: List[JobFileRead] = Field(default_factory=list)


class JobSummary(BaseModel):
    id: str
    user_id: str
    source: str
    destination: str
    state: JobState
    total_files: int
    moved_files: int
    failed_files: int
    created_at: datetime
    updated_at: datetime


class ConsentCreate(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    granted_scope: str = Field(..., min_length=1)
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None


class ConsentRead(BaseModel):
    id: int
    user_id: str
    granted_scope: str
    granted_at: datetime
    expires_at: Optional[datetime] = None


class AuditExport(BaseModel):
    job_id: str
    user_id: str
    granted_scope: str
    events: List[dict]
    files: List[JobFileRead]
