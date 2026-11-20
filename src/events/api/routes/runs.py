from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Response, status

from events.api.dependencies import (
    RunDispatcherDependency,
    RunPostsRepositoryDependency,
    RunsRepositoryDependency,
    UsersRepositoryDependency,
)
from events.api.errors import API_ERROR_RESPONSES
from events.api.schemas import StoredRun, UserId
from events.calendar_export import serialize_calendar
from events.models import Event
from events.persistence.runs_repository import (
    RunLimitReached,
    RunStateConflict,
    RunWeeklyLimitReached,
)

router = APIRouter(
    prefix="/api/users",
    tags=["runs"],
    responses=API_ERROR_RESPONSES,
)


@router.post(
    "/{user_id}/runs",
    response_model=StoredRun,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_user_run(
    user_id: Annotated[UserId, Path()],
    runs_repository: RunsRepositoryDependency,
    users_repository: UsersRepositoryDependency,
    dispatcher: RunDispatcherDependency,
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
        run = await runs_repository.create_run(user_id, settings)
    except RunWeeklyLimitReached as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="The user's rolling 7-day run limit has been reached.",
        ) from exc
    except RunLimitReached as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The user's outstanding run limit has been reached.",
        ) from exc
    if dispatcher is not None:
        dispatcher.notify()
    return run


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


@router.get(
    "/{user_id}/runs/{run_id}/events.json",
    response_model=list[Event],
)
async def get_user_run_events(
    user_id: Annotated[UserId, Path()],
    run_id: Annotated[str, Path(min_length=1)],
    runs_repository: RunsRepositoryDependency,
    run_posts_repository: RunPostsRepositoryDependency,
) -> list[Event]:
    run = await runs_repository.get_run(user_id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run was not found.",
        )
    if "analyze" not in run.checkpoints and run.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run events are not available until analysis completes.",
        )
    return await run_posts_repository.list_events(run_id, user_id)


@router.get(
    "/{user_id}/runs/{run_id}/events.ics",
    response_class=Response,
)
async def get_user_run_calendar(
    user_id: Annotated[UserId, Path()],
    run_id: Annotated[str, Path(min_length=1)],
    runs_repository: RunsRepositoryDependency,
    run_posts_repository: RunPostsRepositoryDependency,
) -> Response:
    run = await runs_repository.get_run(user_id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run was not found.",
        )
    if "analyze" not in run.checkpoints and run.status != "succeeded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run events are not available until analysis completes.",
        )
    events = await run_posts_repository.list_events(run_id, user_id)
    return Response(
        content=serialize_calendar(events, run.settings_snapshot.timezone),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="events.ics"'},
    )


@router.get(
    "/{user_id}/runs/{run_id}/report",
    response_model=dict[str, Any],
)
async def get_user_run_report(
    user_id: Annotated[UserId, Path()],
    run_id: Annotated[str, Path(min_length=1)],
    runs_repository: RunsRepositoryDependency,
) -> dict[str, Any]:
    run = await runs_repository.get_run(user_id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run was not found.",
        )
    if run.report is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Run report is not available yet.",
        )
    return run.report


@router.post(
    "/{user_id}/runs/{run_id}/resume",
    response_model=StoredRun,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_user_run(
    user_id: Annotated[UserId, Path()],
    run_id: Annotated[str, Path(min_length=1)],
    repository: RunsRepositoryDependency,
    dispatcher: RunDispatcherDependency,
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
    if dispatcher is not None:
        dispatcher.notify()
    return run
