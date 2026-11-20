from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from pymongo.asynchronous.database import AsyncDatabase

from events.config import RunExecutionSettings
from events.persistence.events_repository import EventsRepository
from events.persistence.runs_repository import RunsRepository
from events.persistence.users_repository import UsersRepository


def get_database(request: Request) -> AsyncDatabase:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured.",
        )
    return database


def get_users_repository(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> UsersRepository:
    return UsersRepository(database["users"])


def get_events_repository(
    database: Annotated[AsyncDatabase, Depends(get_database)],
) -> EventsRepository:
    return EventsRepository(database["events"])


def get_runs_repository(request: Request) -> RunsRepository:
    database = get_database(request)
    settings: RunExecutionSettings | None = getattr(
        request.app.state,
        "run_execution_settings",
        None,
    )
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Run execution is not configured.",
        )
    return RunsRepository(database["runs"], settings)


UsersRepositoryDependency = Annotated[
    UsersRepository, Depends(get_users_repository)
]
EventsRepositoryDependency = Annotated[
    EventsRepository, Depends(get_events_repository)
]
RunsRepositoryDependency = Annotated[
    RunsRepository, Depends(get_runs_repository)
]
