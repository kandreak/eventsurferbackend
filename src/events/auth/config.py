"""Firebase Admin SDK configuration and initialization.

Local development uses a Firebase service-account JSON file referenced by the
``FIREBASE_SERVICE_ACCOUNT_JSON`` environment variable. Production uses
Application Default Credentials. The Firebase project ID is configured through
``FIREBASE_PROJECT_ID``.
"""

from __future__ import annotations

from typing import Any

import firebase_admin
from firebase_admin import credentials
from google.auth.exceptions import DefaultCredentialsError
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class FirebaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    firebase_project_id: str | None = None
    firebase_service_account_json: str | None = None

    @field_validator("firebase_project_id", "firebase_service_account_json")
    @classmethod
    def blank_values_are_unset(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


def initialize_firebase_app() -> Any:
    """Initialize the Firebase Admin app exactly once.

    Raises ``ValueError`` with a clear message when production configuration is
    missing or invalid.
    """
    settings = FirebaseSettings()
    project_id = settings.firebase_project_id
    if project_id is None:
        raise ValueError(
            "Firebase is not configured. Set FIREBASE_PROJECT_ID and either "
            "FIREBASE_SERVICE_ACCOUNT_JSON for local development or "
            "Application Default Credentials for production."
        )

    try:
        app = firebase_admin.get_app()
    except ValueError:
        app = None

    if app is not None:
        if app.project_id and app.project_id != project_id:
            raise ValueError(
                "The configured FIREBASE_PROJECT_ID does not match the "
                "initialized Firebase app."
            )
        return app

    try:
        if settings.firebase_service_account_json:
            cred = credentials.Certificate(
                settings.firebase_service_account_json
            )
        else:
            cred = credentials.ApplicationDefault()
    except (DefaultCredentialsError, OSError, ValueError) as exc:
        raise ValueError(
            "Firebase Admin credentials could not be loaded. Check the "
            "service-account file or Application Default Credentials."
        ) from exc

    credential_project_id = getattr(cred, "project_id", None)
    if credential_project_id and credential_project_id != project_id:
        raise ValueError(
            "FIREBASE_PROJECT_ID does not match the project ID in the "
            "Firebase service-account file."
        )

    try:
        return firebase_admin.initialize_app(
            cred,
            options={"projectId": project_id},
        )
    except ValueError:
        # Another request may have initialized the default app concurrently.
        app = firebase_admin.get_app()
        if app.project_id and app.project_id != project_id:
            raise ValueError(
                "The configured FIREBASE_PROJECT_ID does not match the "
                "initialized Firebase app."
            )
        return app


def get_firebase_app() -> Any:
    """Return the initialized Firebase Admin app, initializing if needed."""
    initialize_firebase_app()
    return firebase_admin.get_app()
