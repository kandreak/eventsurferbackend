# EventSurfer — Firebase Authentication Implementation

**Status:** Not started  
**Last updated:** 2026-11-25  
**Owner:** Copilot / developer

## 1. Objective

Implement authentication for EventSurfer using Firebase Authentication.

Users must be able to authenticate using:
- Google
- Facebook

Both providers must lead to the **same EventSurfer account** when linked to the same Firebase user.

Firebase is responsible for authentication and identity management. EventSurfer is responsible for application data and authorization.

The backend must identify users exclusively through their verified Firebase UID.

---

## 2. Architectural constraints

These are permanent requirements. Do not change them without explicit approval.

### Authentication and identity

- Firebase Authentication is the only identity provider.
- Use the official `firebase-admin` Python SDK.
- Verify Firebase ID tokens on the backend.
- Use Firebase UID as the canonical `user_id`.
- Google and Facebook must be linked at the Firebase account level.
- Do not implement custom JWT issuance or refresh-token management.
- Do not implement password storage or custom OAuth flows.
- Do not create separate EventSurfer accounts for different login providers.
- Do not store provider credentials, access tokens, or refresh tokens in MongoDB.

### MongoDB

- MongoDB remains the application database.
- Keep existing user-scoped settings, events, runs, cache and other application data.
- Use Firebase UID as the key for existing user-scoped data.
- Do not duplicate Firebase's user or identity database.
- Do not introduce additional collections unless genuinely required by the existing application architecture.
- Preserve existing data and avoid destructive migrations.

### API and authorization

- Protected endpoints must derive the user ID from the verified Firebase token.
- Never trust a user ID supplied independently by the client.
- Migrate user-scoped routes from `/api/users/{user_id}/...` to `/api/users/me/...`.
- Keep `/health` public.
- All other user-specific resources must require authentication.
- A user must never be able to access another user's data, including through run IDs or export endpoints.

### Existing application behaviour

- Do not redesign the event collection or analysis pipeline.
- Do not change the current lookback strategy.
- Do not change Apify, Gemini, deduplication, caching or calendar-generation behaviour.
- Do not redesign the run executor or introduce a separate worker architecture.
- Preserve run creation, stopping, resuming, reporting and checkpoint behaviour.
- Keep the implementation minimal and consistent with the existing project structure.

### Development and security

- Never commit Firebase service-account credentials or secrets.
- Use environment variables and Application Default Credentials where appropriate.
- Automated tests must not require a real Firebase project or credentials.
- Do not introduce a frontend framework just for authentication.
- Do not add an insecure endpoint that accepts credentials or generates tokens.
- Keep development-only authentication helpers out of production routes.

---

## 3. Implementation checklist

Copilot: maintain this checklist as the implementation progresses.

Mark items `[x]` only when implemented and verified. Do not mark an item complete merely because code was written.

### Phase 0 — Inspect and plan

- [ ] Inspect the existing project structure and identify the FastAPI app, routers, dependencies, MongoDB repositories and tests.
- [ ] Identify every endpoint containing a user ID or accessing user-specific data.
- [ ] Identify how user IDs are currently passed into background runs and resume operations.
- [ ] Identify the existing MongoDB user model and determine whether it needs any changes.
- [ ] Write a short implementation plan before making changes.
- [ ] Record any compatibility or migration concerns.

**Acceptance criteria:**
- The plan identifies all affected components and routes.
- No application code has been changed yet.

### Phase 1 — Firebase Admin integration

- [ ] Add `firebase-admin` to project dependencies.
- [ ] Implement centralized Firebase Admin initialization.
- [ ] Support local development with a service-account JSON file.
- [ ] Support production authentication through Application Default Credentials.
- [ ] Configure the Firebase project ID through environment variables.
- [ ] Add configuration validation and useful startup errors.
- [ ] Ensure credentials are excluded from Git.

**Acceptance criteria:**
- Firebase Admin initializes correctly using either supported credential method.
- Missing or invalid configuration produces a clear error.
- No credentials are required by automated tests.

### Phase 2 — Authentication dependency

- [ ] Implement a reusable authenticated-user model.
- [ ] Implement a FastAPI dependency such as `get_current_user`.
- [ ] Extract the Bearer token from the Authorization header.
- [ ] Verify the token using `firebase_admin.auth.verify_id_token`.
- [ ] Reject missing, invalid, expired or incorrectly issued tokens with HTTP 401.
- [ ] Extract the Firebase UID and optional claims such as email.
- [ ] Expose the UID as the canonical application `user_id`.
- [ ] Integrate HTTP Bearer authentication with FastAPI OpenAPI/Swagger.

**Acceptance criteria:**
- Valid Firebase ID tokens resolve to the correct Firebase UID.
- Invalid or missing tokens cannot access protected endpoints.
- Swagger displays an Authorize control.

### Phase 3 — Protect and migrate API routes

- [ ] Migrate `/api/users/{user_id}/...` routes to `/api/users/me/...`.
- [ ] Apply authentication to settings endpoints.
- [ ] Apply authentication to events and JSON/ICS exports.
- [ ] Apply authentication to run creation and listing.
- [ ] Apply authentication to run stop and resume operations.
- [ ] Apply authentication to run status and reports.
- [ ] Apply authentication to run-specific event and calendar exports.
- [ ] Remove reliance on client-supplied user IDs.
- [ ] Preserve `/health` as a public endpoint.
- [ ] Check for any remaining unprotected user-specific routes.

**Acceptance criteria:**
- All user-specific endpoints require a valid Firebase ID token.
- Every operation uses the authenticated Firebase UID.
- No endpoint allows access to another user's data.
- Existing API functionality remains available under the new routes.

### Phase 4 — Background execution and ownership

- [ ] Ensure new runs store the authenticated Firebase UID as their owner.
- [ ] Ensure background execution uses the persisted run owner.
- [ ] Ensure stop and resume operations verify run ownership.
- [ ] Ensure reports and run-specific exports verify ownership.
- [ ] Check that internal execution paths cannot accidentally bypass ownership checks.

**Acceptance criteria:**
- A run always belongs to the user who created it.
- A different authenticated user cannot stop, resume, inspect or export that run.
- Existing checkpoint and resume behaviour remains intact.

### Phase 5 — MongoDB compatibility

- [ ] Confirm existing user-scoped documents use Firebase UID as their user identifier.
- [ ] Identify any required migration for existing development data.
- [ ] Preserve existing settings and event data wherever possible.
- [ ] Avoid introducing duplicate user or identity records.
- [ ] Verify indexes and queries still behave correctly.

**Acceptance criteria:**
- Existing application data remains accessible under the new identity scheme.
- No unnecessary authentication collections or duplicated identity data are introduced.
- Any required migration is documented and reversible where practical.

### Phase 6 — Tests

- [ ] Test missing Authorization header → HTTP 401.
- [ ] Test invalid token → HTTP 401.
- [ ] Test expired token → HTTP 401.
- [ ] Test valid token → correct Firebase UID.
- [ ] Test that `/health` remains public.
- [ ] Test authentication on every protected endpoint group.
- [ ] Test that user A cannot access user B's settings or events.
- [ ] Test that user A cannot access user B's runs, reports or exports.
- [ ] Test run stop and resume ownership.
- [ ] Test existing run execution and resume behaviour.
- [ ] Ensure tests use mocked Firebase verification or FastAPI dependency overrides.

**Acceptance criteria:**
- All relevant automated tests pass.
- Existing tests remain valid or are updated to the new authentication model.
- No real Firebase credentials are needed to run the test suite.

### Phase 7 — Swagger and local development workflow

- [ ] Document how to obtain a Firebase ID token during development.
- [ ] Provide a practical way to authenticate with Google and Facebook locally.
- [ ] If necessary, create a minimal local-only helper page using the Firebase Web SDK.
- [ ] Ensure the helper page is not exposed as a production authentication endpoint.
- [ ] Document how to copy the ID token into Swagger's Authorize control.
- [ ] Verify that authenticated API calls work from `/docs`.

**Acceptance criteria:**
- I can sign in locally using Google or Facebook.
- I can copy the Firebase ID token into Swagger.
- I can use the existing API through Swagger without manually editing requests.

### Phase 8 — Documentation and final review

- [ ] Update README with Firebase setup instructions.
- [ ] Document required environment variables.
- [ ] Document local development credentials.
- [ ] Document production credential configuration.
- [ ] Document the Google/Facebook account-linking behaviour.
- [ ] Document the new `/api/users/me/...` routes.
- [ ] Document how to authenticate through Swagger.
- [ ] Review all architectural constraints in section 2.
- [ ] Run the full test suite.
- [ ] Provide a concise implementation summary and list any remaining manual tasks.

**Acceptance criteria:**
- Another developer can configure Firebase and run the backend by following the documentation.
- No known unprotected user-specific endpoint remains.
- All checklist items are either completed or explicitly documented as pending.

---

## 4. Progress tracking rules for Copilot

This file is the source of truth for implementation progress.

Whenever working on this task:

1. Read this document before making changes.
2. Check the repository to establish the actual current implementation state.
3. Work in phase order unless a dependency requires otherwise.
4. Update this file whenever a checklist item is completed, blocked or changed.
5. Never mark an item complete without verifying its acceptance criteria.
6. If something cannot be completed, leave it unchecked and explain why.
7. If a design change appears necessary, explain the reason and ask for approval before violating an architectural constraint.
8. Keep the `Status` and `Last updated` fields current.
9. Do not delete completed checklist items; preserve them as implementation history.

At the end of each work session, add a short entry below.

---

## 5. Implementation log

### Session log

| Date | Phase | Work completed | Remaining work / blockers |
|---|---|---|---|
| 2026-11-25 | Initial | Architecture and implementation checklist defined | Firebase project setup and implementation pending |

Add a new row after each substantial implementation session.