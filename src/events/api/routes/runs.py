from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from events.api.dependencies import (
    RunsRepositoryDependency,
    UsersRepositoryDependency,
)
from events.api.schemas import StoredRun, UserId
from events.persistence.runs_repository import (
    RunLimitReached,
    RunStateConflict,
)

router = APIRouter(prefix="/api/users", tags=["runs"])


@router.post(
    "/{user_id}/runs",
    response_model=StoredRun,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_user_run(
    user_id: Annotated[UserId, Path()],
    runs_repository: RunsRepositoryDependency,
    users_repository: UsersRepositoryDependency,
) -> StoredRun:
    settings = await users_repository.get_settings(user_id)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User settings were not found.",
        )
    if not settings.sources:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least one source must be configured before starting a run.",
        )
    try:
        return await runs_repository.create_run(user_id, settings)
    except RunLimitReached as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The user's outstanding run limit has been reached.",
        ) from exc


@router.get("/{user_id}/runs", response_model=list[StoredRun])
async def list_user_runs(
    user_id: Annotated[UserId, Path()],
    repository: RunsRepositoryDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[StoredRun]:
    return await repository.list_runs(
        user_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{user_id}/runs/{run_id}", response_model=StoredRun)
async def get_user_run(
    user_id: Annotated[UserId, Path()],
    run_id: Annotated[str, Path(min_length=1)],
    repository: RunsRepositoryDependency,
) -> StoredRun:
    run = await repository.get_run(user_id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run was not found.",
        )
    return run


@router.post(
    "/{user_id}/runs/{run_id}/resume",
    response_model=StoredRun,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_user_run(
    user_id: Annotated[UserId, Path()],
    run_id: Annotated[str, Path(min_length=1)],
    repository: RunsRepositoryDependency,
) -> StoredRun:
    try:
        run = await repository.resume_run(user_id, run_id)
    except RunLimitReached as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The user's outstanding run limit has been reached.",
        ) from exc
    except RunStateConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run was not found.",
        )
    return run
