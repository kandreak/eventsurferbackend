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
- Run management and execution API:
  - `POST /api/users/{user_id}/runs` queues and schedules a run
  - `GET /api/users/{user_id}/runs` and
    `GET /api/users/{user_id}/runs/{run_id}` inspect runs
  - `POST /api/users/{user_id}/runs/{run_id}/resume` requeues a failed run
  - `GET /api/users/{user_id}/runs/{run_id}/report`
  - `GET /api/users/{user_id}/runs/{run_id}/events.json` and
    `GET /api/users/{user_id}/runs/{run_id}/events.ics` retrieve that run's
    results
- Async MongoDB Atlas connection configured through environment variables.
- Startup ping and initial indexes for posts, events, runs, and run-scoped
  collected posts.
- User settings are stored in MongoDB's `users` collection.
- Collected posts and their analysis cache are stored in MongoDB's `posts`
  collection whenever `MONGODB_URI` is configured.
- Deduplicated events are stored in MongoDB's `events` collection and served
  with optional date, source, and status filters.
- Run records, per-user outstanding limits, cross-process execution slots,
  leases, stage checkpoints, run-scoped results, reports, and resume state are
  stored in MongoDB. A dispatcher inside the FastAPI lifespan automatically
  executes queued runs; there is no separate worker command or CLI pipeline.

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

Save a user's sources, then start a run through the API:

```powershell
$base = "http://127.0.0.1:8000"
$userId = "local-user"
$settings = @{
    timezone = "Europe/Rome"
    sources = @(@{
        name = "Example Venue"
        platform = "instagram"
        url = "https://www.instagram.com/example_venue/"
        lookback_days = 7
    })
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Put `
    -Uri "$base/api/users/$userId/settings" `
    -ContentType "application/json" `
    -Body $settings
$run = Invoke-RestMethod -Method Post -Uri "$base/api/users/$userId/runs"
Invoke-RestMethod -Uri "$base/api/users/$userId/runs/$($run.run_id)"
```

Poll run detail until it is `succeeded` or `failed`, then retrieve
`/report`, `/events.json`, or `/events.ics` using that run ID.

Run creation and resume return `202 Accepted`; the FastAPI process dispatches
the work in the background, so the HTTP request does not wait for providers.
Run detail exposes progress and errors. After analysis completes, use the
run-scoped JSON, ICS, and report routes above to retrieve its output.

Event routes accept `date_from`, `date_to`, `source` (exact source name), and
`status` (`active`, `cancelled`, or `archived`) filters. Without a date filter,
JSON includes undated events and ICS preserves them as calendar tasks.

## TODO — build a working backend

Work through these steps in order. Run execution is managed by FastAPI but
kept outside HTTP handlers. MongoDB is the source of truth.

A **run executor** (often called a “worker”) is the backend component that
claims queued work and performs collection, analysis, and publication. It runs
as an internal background task owned by the FastAPI application; it is not a
separate command or process.

1. **Persist posts and analysis cache** — implemented for API runs configured
   with MongoDB Atlas. Analysis is reused only when post content and analysis
   settings match; otherwise the post returns to pending analysis.

2. **Persist and serve events** — implemented: validated, deduplicated results
   are stored in Atlas and served through user-scoped and run-scoped JSON and
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
         "lease_duration_seconds": 60,
         "dispatch_poll_interval_seconds": 1
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
     duration is currently 60 seconds; the worker renews its lease while
     processing a run.
   - Failed runs can be resumed from their last incomplete stage; expired
     leases make interrupted runs claimable again.
   - This step defines the durable run state and admission/lease limits used by
     the executor and dispatcher in steps 4 and 5.

4. **Build the run-execution pipeline — implemented**
   - The internal executor loads the user's settings snapshot and run state
     from MongoDB; shared application settings come from
     `config/settings.json`, and provider credentials come from the
     environment.
   - Collection, analysis, and publication run as separate, checkpointed
     stages. Collected posts and extracted events are stored per run so a
     resumed run can continue without repeating completed stages. Post
     analysis reuses the existing Atlas cache.
   - The worker renews its execution lease while stages run. Provider errors
     are saved in the run report and failure history; JSON, ICS, and report
     results are retrieved through run-scoped API routes. Tests mock provider
     calls by default.

5. **Connect API runs to background execution — implemented**
   - The FastAPI lifespan starts a local dispatcher. Create and resume
     requests wake it; a periodic poll also recovers queued work after a
     process restart.
   - The dispatcher claims runs up to the configured capacity. Atlas-backed
     leases enforce the global and per-user limits across API processes.
   - Cloud Run Jobs integration can be added later if deployment requires it.

6. **Standardize API validation and errors — implemented**
   - Errors use `{"error":{"code":"...","message":"...","details":[...]}}`;
     `details` is included for request validation errors and contains only
     field locations, safe messages, and validation types.
   - Expected HTTP failures preserve their status codes; invalid requests use
     `422`, missing resources use `404`, state conflicts use `409`, and
     unavailable MongoDB services use `503`.
   - Invalid persisted Pydantic data and unexpected exceptions are logged and
     return sanitized `500` responses. MongoDB exceptions are logged and
     reported as `503`; exception details are not sent to clients.
   - Calendar export returns `404` when user settings are missing instead of
     silently assuming a timezone.

7. **Verify user scoping**
   - Audit every repository operation that reads or writes user-owned data.
   - Add tests proving that one user's settings, runs, posts, events, and
     run-scoped results cannot be accessed through another user's ID.

8. **Add Atlas integration coverage**
   - Add opt-in integration tests for persistence, admission limits,
     concurrent claims, lease renewal/expiry, stage checkpoints, and resume.
   - Keep the ordinary test suite isolated from Atlas and mock Apify/Gemini;
     integration tests must use dedicated test records and clean them up.

9. **Add authentication and authorization**
   - Choose an identity provider and map verified identities to application
     users.
   - Enforce authorization on settings, runs, events, and result routes.
   - Complete this before exposing the API as a public multi-user service.

10. **Package the API (optional)**
    - Create one Docker image with the FastAPI app and its in-process
      dispatcher; do not add a separate worker entrypoint.
    - Add a deployment health check and ensure shutdown lets active leases
      expire safely for recovery.

11. **Document and verify local deployment**
    - Document required environment variables, settings, and startup steps.
    - Verify database connectivity, provider configuration, health checks, and
      request/result flows in the packaged environment.

12. **Deploy to Cloud Run (optional)**
    - Deploy only when cloud hosting is wanted and authentication is in place.
    - Configure billing, networking, secrets, and service resources explicitly;
      free allowances do not guarantee zero cost.

## Cost-conscious development

The API and tests can be developed locally with an Atlas free/shared cluster.
Tests use isolated in-memory database doubles and do not require MongoDB Atlas
or call Apify/Gemini. For real API runs, set `MONGODB_URI` to the Atlas
connection string and configure provider credentials in the environment. Live
Apify or Gemini requests may incur charges; do not start a run unless you intend
to make those calls. No Google Cloud deployment is needed for local development.

`user_id` is currently an application-level identifier, not authentication.
Do not expose this development API as a public multi-user service until an
access-control layer is implemented.

## Tests

```powershell
python -m pytest
```
