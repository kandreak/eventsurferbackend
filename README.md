# EventSurfer Backend

EventSurfer is a Python backend for collecting public social-media posts, extracting and deduplicating events, and exposing the results through a REST API and calendar feeds.

The backend is built with FastAPI and MongoDB Atlas. It uses Apify for collection and Gemini for event extraction.

## Current status

The backend currently provides:

- FastAPI health endpoint: `GET /health`.
- Per-user settings:
  - `GET /api/users/me/settings`
  - `PUT /api/users/me/settings`
- User-scoped events:
  - `GET /api/users/me/events`
  - `GET /api/users/me/events.json`
  - `GET /api/users/me/events.ics`
- Run management:
  - `POST /api/users/me/runs` — queue a run.
  - `POST /api/users/me/runs/stop` — stop queued runs and request cooperative cancellation of active runs.
  - `POST /api/users/me/runs/{run_id}/stop` — stop one run.
  - `GET /api/users/me/runs` — list runs.
  - `GET /api/users/me/runs/{run_id}` — inspect a run.
  - `POST /api/users/me/runs/{run_id}/resume` — resume a failed run.
  - `GET /api/users/me/runs/{run_id}/report` — retrieve its report.
  - `GET /api/users/me/runs/{run_id}/events.json` and `events.ics` — retrieve run results.
- MongoDB persistence for user settings, collected posts and analysis cache, deduplicated events, runs, run-scoped results, reports, checkpoints, leases, and resume state.
- A dispatcher started by the FastAPI lifespan that executes queued runs in the API process. There is currently no separate worker command or CLI pipeline.
- API validation and consistent error responses.
- User-data scoping and cross-user isolation tests.

Firebase Authentication is being implemented. User-specific routes now use `/api/users/me/...` and derive the MongoDB user key from a verified Firebase ID token. The local sign-in helper and final setup workflow are still pending, so do not expose this service publicly until the remaining items in [`docs/TODO_AUTHENTICATION.md`](docs/TODO_AUTHENTICATION.md) are complete.

## Requirements

- Python 3.12 (the commands below use the Windows Python launcher).
- A MongoDB Atlas connection string for database-backed API operations.
- Provider credentials for live Apify and Gemini operations.

The default automated test suite does not require Atlas and does not make live Apify or Gemini calls.

## Run locally (Windows / PowerShell)

Create and activate a virtual environment, install the project and test dependencies, and create your local environment file:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
Copy-Item .env.example .env
```

Set `MONGODB_URI` in `.env` to your Atlas connection string. You can optionally set `MONGODB_DATABASE`; it defaults to `eventsurfer`.

Keep `.env` private. It is ignored by Git.

Start the API:

```powershell
uvicorn events.api.app:app --reload
```

The local API is available at `http://127.0.0.1:8000`.

- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`
- Health check: `http://127.0.0.1:8000/health`

## Firebase Admin configuration

In Firebase Console, register the web app, enable Google and Email/Password
sign-in, and add the local development host to the authorized domains. Firebase
owns credentials and provider linking; linking Google and email/password must
use Firebase's client-side account-linking flow, not email-based account
merging.

Set `FIREBASE_PROJECT_ID` to the Firebase project ID. For local development,
set `FIREBASE_SERVICE_ACCOUNT_JSON` to the path of a service-account JSON file
stored outside the repository. The configured project ID must match the
service-account file. Never put service-account credentials in `.env`, source
control, or browser code; `.env` should contain only the path and is ignored by
Git.

In production, omit `FIREBASE_SERVICE_ACCOUNT_JSON`, configure Google
Application Default Credentials (for example, `GOOGLE_APPLICATION_CREDENTIALS`
pointing to a credential file mounted outside the application source), and set
`FIREBASE_PROJECT_ID`. The backend initializes Firebase Admin lazily on the
first authenticated request. Firebase Web App configuration is separate,
client-side configuration and must never contain Admin credentials.

Protected API requests use `Authorization: Bearer <Firebase ID token>`.
`/health` remains public. The development sign-in helper that will make it
easier to obtain and refresh a token is not implemented yet.
In Swagger at `/docs`, select **Authorize** and paste the raw Firebase ID
token (without the `Bearer ` prefix).

Older development examples used the MongoDB key `local-user`. Existing records
under legacy keys are not automatically moved or attached to a Firebase user;
they remain under their current keys. No live Atlas data inventory or
migration has been performed. Do not migrate records without an explicit,
reviewed plan and approval.

If `MONGODB_URI` is not configured, health checks still work; database-backed
routes return `503 Service Unavailable`.

## Basic API workflow

The examples assume `$idToken` is a valid Firebase ID token obtained from a
Firebase client. A local helper is planned but not yet available.

Save a user's sources:

```powershell
$base = "http://127.0.0.1:8000"
$headers = @{ Authorization = "Bearer $idToken" }

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
    -Uri "$base/api/users/me/settings" `
    -ContentType "application/json" `
    -Headers $headers `
    -Body $settings
```

Create a run and inspect it:

```powershell
$run = Invoke-RestMethod -Method Post `
    -Uri "$base/api/users/me/runs" `
    -Headers $headers
Invoke-RestMethod `
    -Uri "$base/api/users/me/runs/$($run.run_id)" `
    -Headers $headers
```

Run creation, resume, and stop return `202 Accepted`. The FastAPI process dispatches work in the background, so the HTTP request does not wait for external providers. Poll run detail until the run is `succeeded` or `failed`, then retrieve its `/report`, `/events.json`, or `/events.ics` output.

Queued runs transition to `stopped` immediately. Active runs stop cooperatively when their current operation next checks its lease. The stop response reports stopped runs and active runs with a stop requested through the `stopped` and `cancellation_requested` counts.

When a run becomes `stopped`, its run-scoped collected posts and event snapshots are deleted. The shared per-user `posts` analysis cache is retained. Already-terminal `succeeded` and `failed` runs are left unchanged.

Event routes accept these optional filters:

- `date_from`
- `date_to`
- `source` — exact source name
- `status` — `active`, `cancelled`, or `archived`

Without a date filter, JSON includes undated events and ICS preserves them as calendar tasks.

## How run execution works

MongoDB is the source of truth for run state. The API's internal dispatcher claims queued work and runs the collection, analysis, and publication stages outside HTTP handlers.

- Run records persist status, stage, progress, checkpoints, settings snapshots, attempts, timestamps, and error history.
- Collection, analysis, and publication are separate checkpointed stages.
- Run-scoped posts and events allow a resumed run to continue without repeating completed stages.
- The analysis cache is reused only when post content and analysis settings match.
- Atlas-backed atomic leases and slots enforce global and per-user execution limits across API processes.
- The dispatcher wakes when runs are created or resumed and also polls periodically to recover queued work after a process restart.

Run limits are configured in the shared application settings (currently `config/settings.json`), not hard-coded. The current configuration includes limits for sources, weekly runs, global and per-user concurrency, outstanding runs, and lease duration. See that file for the active values.

## Configuration and costs

The API can be developed locally with an Atlas free/shared cluster. No Google Cloud deployment is required for local development.

Live Apify and Gemini requests may incur charges. Do not start a real run unless you intend to make those provider calls. Provider credentials belong in environment variables, not source control.

## Tests

Run the default test suite:

```powershell
python -m pytest
```

The default suite uses test doubles and does not require MongoDB Atlas or call Apify/Gemini.

### Optional Atlas integration tests

These tests use a real Atlas connection and are disabled by default. Each test creates and drops its own randomly named temporary database; existing application records are not touched. The Atlas database user needs permission to create and drop databases.

```powershell
$env:RUN_ATLAS_INTEGRATION_TESTS = "1"
python -m pytest tests/integration
Remove-Item Env:RUN_ATLAS_INTEGRATION_TESTS
```

These tests do not call Apify or Gemini.

### Optional Gemini integration tests

The Gemini smoke check reads configured model metadata without generating content:

```powershell
$env:RUN_GEMINI_INTEGRATION_TESTS = "1"
python -m pytest tests/integration/test_gemini_api.py
Remove-Item Env:RUN_GEMINI_INTEGRATION_TESTS
```

An actual generation request is separately opt-in. It sends one synthetic text-only post, uses no images, and disables retries. Google billing must be enabled for the API key:

```powershell
$env:RUN_GEMINI_INTEGRATION_TESTS = "1"
$env:RUN_GEMINI_GENERATION_INTEGRATION_TESTS = "1"
python -m pytest tests/integration/test_gemini_api.py -k synthetic_event
Remove-Item Env:RUN_GEMINI_INTEGRATION_TESTS
Remove-Item Env:RUN_GEMINI_GENERATION_INTEGRATION_TESTS
```

### Optional Apify integration tests

The basic check reads metadata for the configured Instagram actor; it does not start an actor run:

```powershell
$env:RUN_APIFY_INTEGRATION_TESTS = "1"
python -m pytest tests/integration/test_apify_api.py
Remove-Item Env:RUN_APIFY_INTEGRATION_TESTS
```

An actual scrape is separately opt-in. It requires a public profile URL chosen for the test, requests at most one post, and caps the actor charge at `$0.05`:

```powershell
$env:RUN_APIFY_INTEGRATION_TESTS = "1"
$env:RUN_APIFY_SCRAPE_INTEGRATION_TESTS = "1"
$env:APIFY_INTEGRATION_SOURCE_URL = "https://www.instagram.com/example/"
python -m pytest tests/integration/test_apify_api.py -k scraper
Remove-Item Env:RUN_APIFY_INTEGRATION_TESTS
Remove-Item Env:RUN_APIFY_SCRAPE_INTEGRATION_TESTS
Remove-Item Env:APIFY_INTEGRATION_SOURCE_URL
```

The Apify scraper's charge cap is enforced by Apify; provider pricing and other account limits still apply.

## Project documentation

- [`AGENTS.md`](AGENTS.md) — repository-wide instructions for coding agents.
- [`docs/TODO_AUTHENTICATION.md`](docs/TODO_AUTHENTICATION.md) — Firebase authentication implementation checklist.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — optional packaging and deployment work.

## Security note

User-specific API routes require a verified Firebase ID token and derive the
user key from its UID. The local sign-in helper and some setup and verification
work remain incomplete; keep the API local or otherwise access-restricted
until the remaining checklist phases are complete.
