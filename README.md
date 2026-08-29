# ConsentMove

## Problem

Verbatim from serverfault.com (score 16, surfaced in a scan across Ask HN,
Lobsters and seven StackExchange sites on 2026-08-28):

> "How to migrate User Data to OneDrive without granting administrators permission"

Moving one person's files between cloud accounts normally requires an
administrator to hold application-wide impersonation rights — permissions that
let them read every mailbox and every drive in the tenant, long after the one
migration is finished. Security teams resist granting it, auditors flag it, and
in regulated environments it may be refused outright. So the migration either
does not happen, or it happens under a standing grant that nobody revokes.

## Existing tools, and why this is not one of them

Competitor check on 2026-08-28 — "onedrive migration tool no admin", "user data
migration cloud consent" — returned **no rival at all** (verdict OPEN, 0 search
errors). The established migration tools (ShareGate, Mover, BitTitan) all assume
the tenant-wide grant; that assumption is the thing this project removes, not a
feature it competes on.

## Users

IT staff at organisations where a standing impersonation grant is refused or
must be time-boxed, and individuals moving their own data out of an account they
are leaving.

## MVP Scope

- The data owner authorises once via delegated OAuth; no application-wide grant
  is ever requested.
- `POST /jobs {source, destination, scope}` starts a transfer running under that
  delegated token; the token's own expiry bounds the job.
- Resume a partially-completed job without re-authorising, as long as the
  refresh token is still valid — and fail loudly, not silently, when it is not.
- `GET /jobs/{id}` reports per-file progress and every file that failed, with
  the reason.
- Audit record: who consented, when, what scope, which files moved. Exportable
  as JSON for the auditor who asked the question in the first place.

## Out of Scope

No mailbox migration, no SharePoint site collections, no tenant-to-tenant bulk
moves, no scheduling or throttling policy engine. One person's files, one
consent, one auditable job.

## Architecture

FastAPI application, layered:

- `api/` — jobs router and schemas only.
- `services/` — `MigrationService` owns job lifecycle and resume logic;
  `ConsentService` owns the OAuth dance and token refresh.
- `clients/` — one client per cloud provider behind a common interface, so a
  second provider is added without touching the services.
- `repositories/` — job and audit records; the audit record is append-only.

## Acceptance

- A migration completes with only delegated consent — the test must assert that
  no application-level permission is requested anywhere in the flow, and that
  assertion must go red if the code starts requesting one. Both mutation counts
  reported.
- An expired refresh token produces an explicit `needs_reconsent` state, never a
  silent partial success: a job that moved 40 of 100 files must not report done.
- The audit export names the consenting user, the scope granted and every file
  moved; a file that failed appears with its error, not omitted.
- Revoking consent mid-job stops the job at the next file boundary and leaves
  the already-moved files intact and recorded.
