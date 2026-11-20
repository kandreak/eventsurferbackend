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
- Async MongoDB Atlas connection configured through environment variables.
- Startup ping and initial indexes for posts, events, and runs.
- User settings are stored in MongoDB's `users` collection.
- Collected posts and their analysis cache are stored in MongoDB's `posts`
  collection whenever `MONGODB_URI` is configured.
- Deduplicated events are stored in MongoDB's `events` collection and served
  with optional date, source, and status filters.
- Existing collector, extraction, event, and calendar components are retained
  and covered by tests, but are **not yet connected to API-managed runs**.

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

The API does not yet create or execute processing runs. The CLI persists posts
and events to Atlas when `MONGODB_URI` is configured.

## TODO — build a working backend

Work through these steps in order. Keep the API and worker separate, use MongoDB
as the source of truth, and avoid running Apify or Gemini work inside HTTP
handlers.

1. **Persist posts and analysis cache** — implemented for CLI runs configured
   with MongoDB Atlas. Analysis is reused only when post content and analysis
   settings match; otherwise the post returns to pending analysis.

2. **Persist and serve events** — implemented: validated, deduplicated CLI
   results are stored in Atlas and served through user-scoped list, JSON, and
   dynamic ICS routes. Undated events remain in unfiltered JSON and calendar
   output.

3. **Implement run management**
   - Add a runs repository and `POST`/`GET` run endpoints.
   - Persist run status, current stage, progress, checkpoints, timestamps, and
     errors.
   - Add atomic worker leasing/locking and idempotent stage transitions.
   - Implement resume for failed or interrupted runs.

4. **Build the worker pipeline**
   - Add a worker entrypoint that accepts a `run_id` and loads all state and
     settings from MongoDB.
   - Connect the existing collectors and Gemini extraction to independently
     restartable stages.
   - Persist results and checkpoints as stages complete; keep provider errors
     visible in run status and reports.
   - Support local worker execution first. Do not trigger provider calls in
     tests; mocked tests must remain the default.

5. **Connect API runs to background execution**
   - Make run-creation and resume requests schedule work rather than execute
     long-running tasks in request handlers.
   - Use a local development runner initially. Add Cloud Run Jobs integration
     only when deployment is wanted.

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
