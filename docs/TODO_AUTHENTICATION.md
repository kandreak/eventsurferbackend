# Authentication implementation — Firebase

**Status:** In progress  
**Last updated:** 2026-11-27  
**Implementation owner:** Copilot, with manual Firebase Console setup by the developer

## Goal

Add authentication to EventSurfer using Firebase Authentication.

Supported sign-in methods:
- Google
- Email and password (managed entirely by Firebase)

Firebase owns user accounts, credentials, password reset, and identity-provider linking. The backend verifies Firebase ID tokens and uses the verified Firebase UID as the canonical application `user_id`.

There is no Facebook integration in scope.

## Permanent constraints

These are architectural requirements, not checklist items. Do not change them without explicit approval.

### Identity and authentication

- Use Firebase Authentication as the identity provider.
- Use the official `firebase-admin` Python SDK to verify Firebase ID tokens.
- Use Firebase UID as the canonical application `user_id`.
- Support Google and Firebase-managed email/password authentication.
- Do not store passwords, password hashes, provider credentials, Firebase ID tokens, or refresh tokens in MongoDB.
- Do not implement custom JWT issuance, refresh-token management, password reset, or OAuth callback handling in the backend.
- Do not create a separate EventSurfer account for each sign-in method.
- Account linking between Google and email/password must be handled through Firebase's supported client-side account-linking flow. Do not merge accounts merely because their email addresses match.
- Firebase Web App configuration (API key, auth domain, project ID, app ID)
  is client-side configuration, not a service-account credential. Keep it
  separate from backend secrets and never expose service-account credentials
  to the browser.

### MongoDB and existing application data

- MongoDB remains the application database, not the authentication database.
- Use Firebase UID as the user key for application data.
- Preserve existing settings, posts/cache, events, runs, reports, checkpoints, leases, and indexes.
- Do not add a separate identities collection or duplicate Firebase's user directory.
- Do not perform destructive migrations. If existing development data uses a temporary user ID, document a safe migration or retain it as clearly identified development data.

### API and authorization

- Derive the authenticated user ID exclusively from the verified Firebase token.
- Never trust a caller-supplied user ID for a protected operation.
- Migrate user routes from `/api/users/{user_id}/...` to `/api/users/me/...`.
- Keep `/health` public.
- Protect every user-specific endpoint, including run actions, reports, and JSON/ICS exports.
- Verify run ownership for every operation addressed by `run_id`.

### Existing behaviour and scope

- Do not change the event collection or analysis pipeline.
- Do not change the existing lookback strategy.
- Do not change Apify, Gemini, deduplication, caching, calendar generation, or run-execution behaviour.
- Do not add a separate worker process.
- Keep the implementation focused and consistent with the existing codebase.
- Automated tests must not require live Firebase credentials.
- The local sign-in helper is development-only and must not be exposed as
  a production authentication interface.
- Do not introduce a frontend build system or additional infrastructure
  solely for the local Swagger sign-in helper unless genuinely necessary.

## Developer setup checklist (Firebase Console)

These steps are completed manually by the repository owner.

- [x] Register the EventSurfer Web app in the Firebase project.
- [x] Record the Firebase project ID and Web App configuration.
- [x] Enable Google as a sign-in provider.
- [x] Enable Email/Password as a sign-in provider.
- [ ] Configure the Firebase authorized domains needed for local development (including `localhost` if required).
- [ ] Confirm the Firebase project is in the intended billing/plan state before using any paid services.
- [x] Keep any service-account credentials outside the repository and out of version control.

Do not enable Facebook for this implementation.

## Copilot implementation checklist

Copilot must inspect the current repository before editing. Update this checklist as work is completed. Mark an item `[x]` only after verifying its acceptance criteria.

### Phase 0 — Inspect and plan

- [x] Inspect the FastAPI application, routers, dependencies, MongoDB repositories, configuration, and tests.
- [x] Inventory every endpoint that reads or writes user-specific data.
- [x] Trace how `user_id` is passed into run creation, execution, stop, resume, reporting, and exports.
- [x] Inspect how existing user records and development data are keyed.
- [x] Identify the least disruptive way to switch authenticated requests to Firebase UID.
- [x] Write a concise implementation plan and identify any data migration or compatibility concerns.

**Acceptance criteria**
- The plan identifies all affected endpoints and code paths.
- No application code has been changed during this inspection phase.

### Phase 1 — Firebase Admin setup

- [x] Add `firebase-admin` to project dependencies.
- [x] Implement one centralized Firebase Admin initialization path.
- [x] Support local development using a service-account JSON file referenced by an environment variable or the existing project configuration convention.
- [x] Support production credentials through Application Default Credentials.
- [x] Configure and validate the Firebase project ID.
- [x] Ensure initialization is safe across the FastAPI application lifecycle and does not create duplicate Firebase app instances.
- [x] Update `.env.example` with variable names and safe placeholders only.
- [x] Ensure credentials are excluded from Git.
- [x] Keep Firebase Web App configuration separate from Firebase Admin
      credentials; document which environment variables are public
      client configuration and which are private backend configuration.

**Acceptance criteria**
- Local and production credential strategies are documented.
- Invalid or missing production configuration fails clearly when authentication is initialized.
- No real credentials are needed by the default test suite.
- No credential material is committed.

### Phase 2 — Token verification and authenticated principal

- [x] Add a small authenticated-principal model containing at least `user_id` and optional verified claims such as email.
- [x] Implement a reusable FastAPI dependency such as `get_current_user`.
- [x] Read the Firebase ID token from `Authorization: Bearer <token>`.
- [x] Verify the token using `firebase_admin.auth.verify_id_token`.
- [x] Reject missing, malformed, invalid, expired, or otherwise unverifiable tokens with HTTP 401.
- [x] Use only the verified token's `uid` as `user_id`.
- [x] Avoid logging bearer tokens or sensitive claims.
- [x] Configure FastAPI HTTP Bearer security so Swagger/OpenAPI exposes an Authorize control.

**Acceptance criteria**
- A valid Firebase ID token resolves to its Firebase UID.
- Missing and invalid tokens receive HTTP 401.
- Swagger shows the Bearer Authorize control.
- The dependency is straightforward to override in tests.

### Phase 3 — Protect and migrate API routes

- [x] Migrate user-scoped routes from `/api/users/{user_id}/...` to `/api/users/me/...`.
- [x] Protect settings read and update routes.
- [x] Protect user events and JSON/ICS exports.
- [x] Protect run creation and listing.
- [x] Protect stop-all and per-run stop operations.
- [x] Protect run detail and resume operations.
- [x] Protect run reports and run-scoped JSON/ICS exports.
- [x] Remove request-controlled user IDs from protected route contracts.
- [x] Keep `/health` public.
- [x] Search for and protect any additional user-specific routes not listed above.

**Acceptance criteria**
- Every user-specific route requires a verified Firebase ID token.
- Every repository operation uses the authenticated UID as its user scope.
- No protected endpoint accepts a caller-selected user ID as authority.
- Existing endpoint functionality and response semantics are preserved, apart from the intentional route/authentication change.

### Phase 4 — Run ownership and background execution

- [x] Ensure a newly created run stores the authenticated Firebase UID as its owner.
- [x] Ensure the dispatcher and executor use the owner persisted on the run.
- [x] Ensure stop, resume, detail, report, and result-export operations verify both run ID and authenticated owner.
- [x] Check internal execution and recovery paths for assumptions that user IDs are caller-supplied.
- [x] Preserve current leases, checkpoints, concurrency limits, and resume semantics.

**Acceptance criteria**
- A run's owner is established at creation and cannot be changed by a later request.
- Another authenticated user cannot inspect, stop, resume, or export the run.
- Background execution continues to work independently of the original HTTP request.
- Existing run lifecycle behaviour remains intact.

### Phase 5 — Existing MongoDB data compatibility

- [x] Confirm all user-scoped repository queries and writes use Firebase UID after authentication is introduced.
- [ ] Identify whether existing development records keyed by `local-user` or another temporary ID need migration.
- [x] Do not silently attach existing data to the first user who logs in.
- [ ] If migration is needed, document a deliberate, explicit, safe migration procedure.
- [x] Verify existing indexes remain appropriate.

**Compatibility note**
- Older README examples used `local-user`, but this is not evidence that such records exist in Atlas. No live database inventory was performed. Any legacy records remain untouched and are not migrated or reassigned. Determine actual data and any migration need with the owner before planning a migration.

**Acceptance criteria**
- No cross-user data leakage is possible.
- Existing data is not silently reassigned or deleted.
- Any required migration is documented before it is run.

### Phase 6 — Tests

- [x] Test missing Authorization header returns HTTP 401.
- [x] Test malformed and invalid tokens return HTTP 401.
- [x] Test expired/unverifiable tokens return HTTP 401.
- [x] Test a valid token resolves to the expected Firebase UID.
- [x] Test `/health` remains public.
- [x] Test authentication on every protected endpoint group.
- [x] Test one user cannot access another user's settings or events.
- [x] Test one user cannot access another user's runs, reports, or exports.
- [x] Test run stop and resume ownership checks.
- [x] Test existing run execution, checkpoint, and resume behaviour.
- [x] Use mocked Firebase verification or FastAPI dependency overrides; do not call Firebase in the default test suite.

**Acceptance criteria**
- Relevant unit and API tests pass.
- Existing tests are updated for the new route and authentication contracts.
- The default test suite requires no Firebase project or credentials.

### Phase 7 — Local sign-in and Swagger workflow

- [ ] Provide a minimal local-only sign-in helper using the Firebase Web SDK.
- [ ] Support Google sign-in and email/password sign-up and sign-in.
- [ ] Use the Firebase Web App configuration, never Admin SDK credentials,
      in the browser.
- [ ] Allow the developer to retrieve and copy the current Firebase ID token.
- [ ] Ensure the helper can obtain a refreshed ID token when the previous
      one has expired.
- [ ] Serve or expose the helper only in local development.
- [x] Do not introduce an insecure production backend endpoint for login
      or token generation.
- [x] Configure Swagger/OpenAPI with HTTP Bearer authentication.
- [x] Document how to use the copied token in Swagger's Authorize control.
- [ ] Verify authenticated requests work through `/docs`.

**Acceptance criteria**
- The developer can sign in locally using Google or email/password.
- The developer can copy a valid Firebase ID token into Swagger.
- Protected API calls work from Swagger.
- Expired tokens can be refreshed through the Firebase client SDK.
- The helper is unavailable in production.
- No token-generation or credential-bypass endpoint is introduced.

### Phase 8 — Documentation and final verification

- [x] Update README to describe the implemented authentication state accurately.
- [x] Document Firebase project setup and enabled providers.
- [x] Document required environment variables and local credentials.
- [x] Document production credential configuration.
- [x] Document Google and email/password account-linking behaviour.
- [x] Document `/api/users/me/...` routes and Swagger authentication.
- [x] Update `.env.example` without adding secrets.
- [x] Run the complete default test suite.
- [x] Review all permanent constraints in this document.
- [x] Summarize completed work, tests run, migration needs, and remaining manual steps.
- [ ] Document the local sign-in helper URL and its development-only
      availability.
- [x] Document the distinction between Firebase Web App configuration
      and Firebase Admin service-account credentials.

**Acceptance criteria**
- A developer can configure Firebase and run the authenticated backend by following the documentation.
- No known user-specific endpoint is left unprotected.
- All checklist items are either verified complete or explicitly left pending with an explanation.

**Audit notes**
- Phase 5 data inventory and any migration procedure remain pending: no live MongoDB/Atlas records were inspected, and no records were changed or reassigned.
- Phase 7's local sign-in helper and its URL do not exist yet. A real authenticated request through `/docs` also remains unverified because no Firebase project credentials or live token were used; Swagger's Bearer security metadata and mocked auth behavior are tested.
- Firebase Console provider setup remains a developer action. The overall local sign-in acceptance criteria therefore remain incomplete until the helper is implemented and verified.

## Copilot session rules

1. Read this file and `AGENTS.md` before continuing authentication work.
2. Inspect the actual repository state at the start of each session; do not assume prior notes are still accurate.
3. Work in phase order unless a dependency requires otherwise.
4. Update this checklist and the implementation log after each substantial work session.
5. Do not mark an item complete until its acceptance criteria have been verified.
6. Leave blocked or unverified items unchecked and explain the blocker.
7. Do not weaken permanent constraints without asking the developer.
8. Keep the status and last-updated fields current.

## Implementation log

| Date | Phase | Work completed | Remaining work / blockers |
|---|---|---|---|
| 2026-11-26 | Initial | Scope changed to Google + Firebase email/password; Facebook removed | Firebase provider setup and implementation pending |
| 2026-11-27 | Phase 1 | Added Firebase Admin configuration for local service-account files and production ADC; validates project IDs, reuses the default app, documents configuration, and ignores common credential filenames. Migrated API request tests to `/api/users/me` and added mocked auth/config tests for existing authentication groundwork. | Firebase Console setup, complete auth/ownership test coverage, development sign-in helper, and remaining checklist phases pending. |
| 2026-11-27 | Phase 2 | Completed the authenticated principal and Firebase ID-token dependency; added bearer handling, safe 401 responses, Swagger security metadata, and tests for missing/malformed/invalid/unverifiable tokens, UID extraction, and dependency overrides. | Phases 3–8 and manual Firebase Console setup remain. |
| 2026-11-27 | Phases 3–8 audit | Verified protected `/api/users/me/...` routes, owner-scoped run operations/background execution, UID-scoped persistence/indexes, and existing run behavior. Added auth coverage asserting every protected user route rejects missing credentials; clarified README setup, Swagger token use, account linking, and legacy-data behavior. Full suite: 85 passed, 12 skipped. | No live MongoDB inventory or migration; Firebase Console setup and local sign-in helper remain. End-to-end valid-token Swagger flow not verified. |
