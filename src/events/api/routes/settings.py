from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from events.api.dependencies import UsersRepositoryDependency
from events.api.schemas import UserId, UserSettings

router = APIRouter(prefix="/api/users", tags=["user settings"])


@router.get("/{user_id}/settings", response_model=UserSettings)
async def get_user_settings(
    user_id: Annotated[UserId, Path()],
    repository: UsersRepositoryDependency,
) -> UserSettings:
    settings = await repository.get_settings(user_id)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User settings were not found.",
        )
    return settings


@router.put("/{user_id}/settings", response_model=UserSettings)
async def put_user_settings(
    user_id: Annotated[UserId, Path()],
    settings: UserSettings,
    repository: UsersRepositoryDependency,
) -> UserSettings:
    return await repository.put_settings(user_id, settings)
