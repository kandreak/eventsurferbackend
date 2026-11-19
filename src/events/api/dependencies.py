from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from pymongo.asynchronous.database import AsyncDatabase

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


UsersRepositoryDependency = Annotated[
    UsersRepository, Depends(get_users_repository)
]
