# Project roadmap

This file tracks work that is not part of the core event-processing implementation or the Firebase authentication task.

Items are proposals, not commitments. Keep this document short and update it when priorities change.

## Deployment and operations

### Docker packaging

- [ ] Decide whether Docker packaging is needed for the chosen hosting platform.
- [ ] If needed, create one Docker image for the FastAPI application and its in-process dispatcher.
- [ ] Add a deployment health check.
- [ ] Verify graceful shutdown and lease-expiry recovery for active runs.
- [ ] Document container configuration and local build/run commands.

Constraints:
- Do not introduce a separate worker entrypoint solely for packaging.
- Preserve the current in-process dispatcher unless the deployment requirements explicitly justify a change.

### Cloud Run deployment

- [ ] Choose the target Google Cloud project and region.
- [ ] Review billing, budgets, quotas, and cost controls before deployment.
- [ ] Configure secrets and environment variables without committing credentials.
- [ ] Configure MongoDB connectivity and network access.
- [ ] Set service resources, concurrency, timeout, and scaling deliberately.
- [ ] Deploy only after authentication and authorization are implemented.
- [ ] Verify health checks, authenticated API requests, run execution, and shutdown/recovery behaviour.
- [ ] Document deployment and rollback procedures.

Cost note: free allowances do not guarantee zero cost. Review actual pricing and billing controls before enabling deployed services.

## Roadmap maintenance

- Keep active implementation checklists in their own `docs/TODO_*.md` files.
- Move completed work out of active TODO lists only when its completion is recorded appropriately.
- Do not use this roadmap as a substitute for issue tracking if the project later adopts GitHub Issues or Projects.
