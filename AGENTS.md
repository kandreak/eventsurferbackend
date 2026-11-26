# Agent instructions

These instructions apply to all coding agents working in this repository.

## Project goals

EventSurfer is a Python/FastAPI backend that collects public social posts, extracts events, deduplicates them, and exposes event data and calendar exports. MongoDB is the durable source of truth for user data and run state.

Read `README.md` for the current project overview and local development instructions. Before starting a task, inspect the relevant code and tests rather than assuming the documentation is perfectly current.

## Working principles

- Prefer small, focused changes that fit the existing architecture.
- Preserve working behaviour unless the task explicitly asks to change it.
- Avoid unnecessary frameworks, abstractions, services, dependencies, and infrastructure.
- Do not redesign the collection, analysis, caching, deduplication, calendar, or run-execution pipeline as part of unrelated work.
- Do not change the configured lookback strategy unless explicitly requested.
- Do not introduce a separate worker process or deployment architecture without an explicit requirement.
- Prefer established, maintained Python libraries over custom implementations when they fit the requirement.
- Keep public API behaviour, response schemas, error handling, and persisted data compatible unless a migration is part of the task.
- Add or update tests for behaviour changes. Prefer deterministic unit tests and mocked external providers.
- Never make live Apify, Gemini, Firebase, or other paid/external calls in the default test suite.
- Keep documentation aligned with the actual implementation. Do not document planned functionality as if it already exists.

## Architecture and data safety

- MongoDB is the source of truth for persisted application state.
- Preserve user scoping in every repository query and write.
- Any operation on a run or run result must verify ownership, not merely possession of a run ID.
- Treat external provider responses and scraped content as untrusted input.
- Validate data at system boundaries and avoid leaking secrets, provider payloads, stack traces, or sensitive configuration in API responses and logs.
- Do not commit `.env` files, API keys, service-account files, OAuth secrets, or other credentials.
- Do not perform destructive data migrations or delete persisted data without explicit approval.
- Keep configuration in the existing configuration files and environment-variable conventions; inspect the current implementation before adding new settings.

## Authentication direction

The intended authentication provider is Firebase Authentication, using:
- Google sign-in.
- Firebase-managed email/password sign-in.

Firebase owns authentication credentials and identity management. The backend verifies Firebase ID tokens using the official Firebase Admin Python SDK. The verified Firebase UID is the canonical application user ID.

Do not add Facebook login, custom password storage, custom JWT issuance, custom refresh-token management, or a separate identity database unless explicitly requested.

Do not trust caller-supplied user IDs for authenticated requests. User identity must come from the verified Firebase token. See `docs/TODO_AUTHENTICATION.md` for the current implementation checklist and its acceptance criteria.

## How to work on tasks

1. Read this file and the relevant project documentation.
2. Inspect the current implementation, related tests, and Git status.
3. For substantial work, propose a short plan before editing.
4. Implement the smallest coherent change.
5. Run the relevant tests and checks; report anything that could not be run.
6. Update the relevant documentation and task checklist.
7. Summarize changed files, behaviour changes, tests run, and remaining work.

## Checklist discipline

- Task checklists belong in `docs/`, not in this file or the README.
- Treat the relevant checklist as the source of truth for task progress.
- Mark an item complete only after its acceptance criteria have been verified.
- Keep blocked or unverified items unchecked and explain the blocker.
- Do not silently weaken or remove architectural constraints to make a checklist pass.
- Do not invent completed work or claim tests passed unless they were actually run.

## Coding style

- Follow the conventions already present in the repository.
- Keep functions and modules focused and avoid speculative generalization.
- Use type hints where consistent with neighbouring code.
- Handle expected failures explicitly; let unexpected failures reach the existing centralized error handling.
- Avoid broad exception swallowing and unnecessary logging.
- Keep comments focused on non-obvious reasoning rather than narrating code.
