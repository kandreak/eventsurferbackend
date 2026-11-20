# Eventsurfer Backend

A single Python project for a multi-user events aggregation backend. The
backend is being built around the existing event models, Apify collectors,
Gemini extractor, event deduplication, calendar generation, and reporting
logic.

## Current status

- FastAPI health endpoint: `GET /health`.
- Per-user settings API:
  - `GET /api/users/{user_id}/settings`
  - `PUT /api/users/{user_id}/settings`
- User-scoped event API:
  - `GET /api/users/{user_id}/events`
  - `GET /api/users/{user_id}/events.json`
  - `GET /api/users/{user_id}/events.ics`
- Run management API:
  - `POST /api/users/{user_id}/runs` queues a run
  - `GET /api/users/{user_id}/runs` and
    `GET /api/users/{user_id}/runs/{run_id}` inspect runs
  - `POST /api/users/{user_id}/runs/{run_id}/resume` requeues a failed run
- Async MongoDB Atlas connection configured through environment variables.
- Startup ping and initial indexes for posts, events, and runs.
- User settings are stored in MongoDB's `users` collection.
- Collected posts and their analysis cache are stored in MongoDB's `posts`
  collection whenever `MONGODB_URI` is configured.
- Deduplicated events are stored in MongoDB's `events` collection and served
  with optional date, source, and status filters.
- Run records, per-user outstanding limits, cross-process execution slots,
  leases, stage checkpoints, and resume state are stored in MongoDB. The run
  executor that performs collection and analysis is not implemented yet, so
  newly queued runs remain queued.

## Run locally

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
Copy-Item .env.example .env
```

Set `MONGODB_URI` in `.env` to the Atlas connection string and, optionally,
set `MONGODB_DATABASE` (defaults to `eventsurfer`). Keep `.env` private; it is
ignored by Git. Then start the API:

```powershell
uvicorn events.api.app:app --reload
```

The local API is available at `http://127.0.0.1:8000`; interactive docs are at
`/docs`. When `MONGODB_URI` is not configured, health checks still work, while
database-backed routes return `503 Service Unavailable`.

Event routes accept `date_from`, `date_to`, `source` (exact source name), and
`status` (`active`, `cancelled`, or `archived`) filters. Without a date filter,
JSON includes undated events and ICS preserves them as calendar tasks.

The API can queue and manage runs but does not execute them yet. The CLI
persists posts and events to Atlas when `MONGODB_URI` is configured.

## TODO — build a working backend

Work through these steps in order. Keep the API and run executor separate, use
MongoDB as the source of truth, and avoid running Apify or Gemini work inside
HTTP handlers.

A **run executor** (often called a “worker”) is the backend component that
claims a queued run and performs its collection, analysis, and publishing
stages. This component does not exist yet. Today, the CLI pipeline in
[`src/events/main.py`](./src/events/main.py) runs those stages directly.

1. **Persist posts and analysis cache** — implemented for CLI runs configured
   with MongoDB Atlas. Analysis is reused only when post content and analysis
   settings match; otherwise the post returns to pending analysis.

2. **Persist and serve events** — implemented: validated, deduplicated CLI
   results are stored in Atlas and served through user-scoped list, JSON, and
   dynamic ICS routes. Undated events remain in unfiltered JSON and calendar
   output.

3. **Run management foundation — implemented**
   - User-scoped create, list, detail, and resume endpoints use a runs
     repository. Run records store status, stage, progress, checkpoints,
     settings snapshots, attempts, timestamps, and error history.
   - Configure run limits in shared application settings (for example,
     `config/settings.json`), not hard-coded:
     ```json
     {
       "run_execution": {
         "max_concurrent_runs": 5,
         "max_concurrent_runs_per_user": 1,
         "max_outstanding_runs_per_user": 1,
         "lease_duration_seconds": 60
       }
     }
     ```
     `max_concurrent_runs` limits runs executing globally;
     `max_concurrent_runs_per_user` limits runs executing for one user; and
     `max_outstanding_runs_per_user` limits that user's queued plus running
     runs. Validate positive values and ensure the per-user running limit does
     not exceed the global running limit.
   - Keep the active execution limits separate from the per-user outstanding
     limit. With the current values, a user can have one outstanding run. If
     `max_outstanding_runs_per_user` is later raised to `3`, allow up to three
     of that user's runs to be queued or running; reject the next request with
     `409 Conflict`. Still execute at most one run for that user at a time.
   - Enforce the global running limit across all run executors using
     Atlas-backed atomic leases/slots, not only an in-process semaphore.
     Queued runs do not consume a global running slot. Enforce the per-user
     outstanding limit atomically when creating a run, and the per-user
     execution limit when an executor claims queued work.
   - Give each lease an owner token, heartbeat, and expiry. Require the current
     token for progress/checkpoint updates so an expired worker cannot update a
     run after another executor has claimed it. Stage transitions are
     conditional on the expected current stage; pipeline writes must be
     repeatable when the executor is added in step 4.
     `lease_duration_seconds` is the time allowed between heartbeats, not a
     maximum run duration. If an executor stops renewing its lease, it expires
     and another executor can reclaim the interrupted run. The configured
     duration is currently 60 seconds; automatic heartbeats will be active once
     the executor is implemented.
   - Failed runs can be resumed from their last incomplete stage; expired
     leases make interrupted runs claimable again.
   - Runs can be queued and claimed, but no executor process performs their
     collection or analysis yet; that is the work in steps 4 and 5.

4. **Build the run-execution pipeline**
   - Add an executor entrypoint that accepts a `run_id` and loads all state and
     settings from MongoDB.
   - Connect the existing collectors and Gemini extraction to independently
     restartable stages.
   - Persist results and checkpoints as stages complete; keep provider errors
     visible in run status and reports.
   - Support local run execution first. Do not trigger provider calls in
     tests; mocked tests must remain the default.

5. **Connect API runs to background execution**
   - Make run-creation and resume requests schedule work rather than execute
     long-running tasks in request handlers; runs beyond the configured active
     capacity remain queued.
   - Use a local development run executor initially. Add Cloud Run Jobs
     integration only when deployment is wanted.

6. **Finish API behavior and harden**
   - Implement run history, run detail, and resume routes.
   - Standardize validation and error responses; enforce `user_id` scoping in
     every repository operation.
   - Add integration tests for persistence, concurrency/leases, resuming, and
     user isolation.
   - Add authentication and authorization before exposing the API publicly.

7. **Package and deploy (optional)**
   - Add one Docker image with separate API and worker entrypoints.
   - Document local deployment and verify resource, network, and secret
     configuration.
   - Deploy to Cloud Run only when ready: it requires a linked Google Cloud
     billing account, and free allowances do not guarantee zero cost.

## Cost-conscious development

The API and tests can be developed locally with an Atlas free/shared cluster.
Tests use isolated in-memory database doubles and do not require MongoDB Atlas
or call Apify/Gemini. For real CLI runs, set `MONGODB_URI` to the Atlas
connection string; post persistence, analysis caching, and event persistence
then use that configured database. Live Apify or Gemini requests may incur
charges; do not run them without intentionally choosing to do so. No Google
Cloud deployment is needed for local development.

`user_id` is currently an application-level identifier, not authentication.
Do not expose this development API as a public multi-user service until an
access-control layer is implemented.

## Tests

```powershell
python -m pytest
```
