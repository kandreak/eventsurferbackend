from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response, status

from events.api.dependencies import (
    EventsRepositoryDependency,
    UsersRepositoryDependency,
)
from events.api.schemas import StoredEvent, UserId
from events.calendar_export import serialize_calendar
from events.models import EventStatus

router = APIRouter(prefix="/api/users", tags=["events"])


async def _list_events(
    user_id: str,
    repository: EventsRepositoryDependency,
    *,
    date_from: date | None,
    date_to: date | None,
    source: str | None,
    event_status: EventStatus | None,
) -> list[StoredEvent]:
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_from must be on or before date_to.",
        )
    return await repository.list_events(
        user_id,
        date_from=date_from,
        date_to=date_to,
        source=source,
        status=event_status,
    )


@router.get("/{user_id}/events", response_model=list[StoredEvent])
async def list_user_events(
    user_id: Annotated[UserId, Path()],
    repository: EventsRepositoryDependency,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    event_status: Annotated[
        EventStatus | None, Query(alias="status")
    ] = None,
) -> list[StoredEvent]:
    return await _list_events(
        user_id,
        repository,
        date_from=date_from,
        date_to=date_to,
        source=source,
        event_status=event_status,
    )


@router.get("/{user_id}/events.json", response_model=list[StoredEvent])
async def export_user_events_json(
    user_id: Annotated[UserId, Path()],
    repository: EventsRepositoryDependency,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    event_status: Annotated[
        EventStatus | None, Query(alias="status")
    ] = None,
) -> list[StoredEvent]:
    return await _list_events(
        user_id,
        repository,
        date_from=date_from,
        date_to=date_to,
        source=source,
        event_status=event_status,
    )


@router.get("/{user_id}/events.ics", response_class=Response)
async def export_user_events_ics(
    user_id: Annotated[UserId, Path()],
    repository: EventsRepositoryDependency,
    users_repository: UsersRepositoryDependency,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    source: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
    event_status: Annotated[
        EventStatus | None, Query(alias="status")
    ] = None,
) -> Response:
    events = await _list_events(
        user_id,
        repository,
        date_from=date_from,
        date_to=date_to,
        source=source,
        event_status=event_status,
    )
    settings = await users_repository.get_settings(user_id)
    timezone = settings.timezone if settings is not None else "Europe/Rome"
    return Response(
        content=serialize_calendar(events, timezone),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="events.ics"'},
    )
