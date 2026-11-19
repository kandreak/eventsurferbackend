# Events Aggregator Backend

This repository is the FastAPI backend foundation for the existing Events
Aggregator. It carries forward the original Apify collectors, Gemini
extractor, event models, deduplication, calendar export, reports, CLI, and
their tests. The HTTP API is being added incrementally around that working
pipeline.

## Current API slice

- `GET /health` checks that the service is responding.
- `GET /api/users/{user_id}/settings` retrieves a user's settings.
- `PUT /api/users/{user_id}/settings` creates or replaces a user's settings.
- MongoDB connection initialization and indexes for posts, events, and runs.

Run the API locally:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
Copy-Item .env.example .env
```

Set `MONGODB_URI` and, optionally, `MONGODB_DATABASE` in `.env`, then start
the service:

```powershell
uvicorn events.api.app:app --reload
```

Interactive API documentation is available at `/docs`. Without a MongoDB URI,
the service starts for local health checks, while database-backed endpoints
return `503 Service Unavailable`. Settings data is stored in the `users`
collection; source URLs and lookback periods are validated before persistence.

`user_id` is an application-level identifier only. Authentication and
authorization are not implemented. Do not expose this development API publicly
until an access-control layer is added.

## Existing pipeline

The original pipeline modules remain available, including the CLI entrypoint
(`python -m events.main`). It still expects its JSON config and local output
directory, as before; that CLI has not yet been wired to MongoDB. Configure
provider credentials through environment variables, never through user
settings.

## Next implementation phases

The MongoDB connection and indexes are the persistence foundation. Next steps
are MongoDB-backed post/event repositories and cache integration, followed by
run checkpoints and worker locking, run/event/calendar endpoints, and finally
Cloud Run deployment. HTTP handlers must not run Apify or Gemini processing.

## Tests

```powershell
python -m pytest
```

Tests use mocked provider calls and an in-memory repository; they do not
contact MongoDB, Apify, or Gemini.
