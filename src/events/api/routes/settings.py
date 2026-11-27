from fastapi import APIRouter, HTTPException, status

from events.api.dependencies import CurrentUser, UsersRepositoryDependency
from events.api.errors import API_ERROR_RESPONSES
from events.api.schemas import UserSettings
from events.persistence.users_repository import SourceLimitReached

router = APIRouter(
    prefix="/api/users",
    tags=["user settings"],
    responses=API_ERROR_RESPONSES,
)


@router.get("/me/settings", response_model=UserSettings)
async def get_user_settings(
    current_user: CurrentUser,
    repository: UsersRepositoryDependency,
) -> UserSettings:
    settings = await repository.get_settings(current_user.user_id)
    if settings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User settings were not found.",
        )
    return settings


@router.put("/me/settings", response_model=UserSettings)
async def put_user_settings(
    current_user: CurrentUser,
    settings: UserSettings,
    repository: UsersRepositoryDependency,
) -> UserSettings:
    try:
        return await repository.put_settings(current_user.user_id, settings)
    except SourceLimitReached as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
