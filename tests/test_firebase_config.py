from types import SimpleNamespace

import pytest

from events.auth import config as firebase_config


def test_firebase_initialization_requires_a_project_id(monkeypatch):
    monkeypatch.setattr(
        firebase_config,
        "FirebaseSettings",
        lambda: SimpleNamespace(
            firebase_project_id=None,
            firebase_service_account_json=None,
        ),
    )

    with pytest.raises(ValueError, match="FIREBASE_PROJECT_ID"):
        firebase_config.initialize_firebase_app()


def test_firebase_initialization_uses_application_default_credentials(
    monkeypatch,
):
    initialized: list[tuple[object, dict[str, str]]] = []
    credential = object()
    app = SimpleNamespace(project_id="eventsurfer-prod")
    monkeypatch.setattr(
        firebase_config,
        "FirebaseSettings",
        lambda: SimpleNamespace(
            firebase_project_id="eventsurfer-prod",
            firebase_service_account_json=None,
        ),
    )
    monkeypatch.setattr(
        firebase_config.firebase_admin,
        "get_app",
        lambda: (_ for _ in ()).throw(ValueError("not initialized")),
    )
    monkeypatch.setattr(
        firebase_config.credentials,
        "ApplicationDefault",
        lambda: credential,
    )
    monkeypatch.setattr(
        firebase_config.firebase_admin,
        "initialize_app",
        lambda supplied_credential, options: (
            initialized.append((supplied_credential, options)) or app
        ),
    )

    result = firebase_config.initialize_firebase_app()

    assert result is app
    assert initialized == [
        (credential, {"projectId": "eventsurfer-prod"})
    ]


def test_service_account_project_must_match_configured_project(monkeypatch):
    monkeypatch.setattr(
        firebase_config,
        "FirebaseSettings",
        lambda: SimpleNamespace(
            firebase_project_id="eventsurfer-prod",
            firebase_service_account_json="C:\\secure\\service-account.json",
        ),
    )
    monkeypatch.setattr(
        firebase_config.firebase_admin,
        "get_app",
        lambda: (_ for _ in ()).throw(ValueError("not initialized")),
    )
    monkeypatch.setattr(
        firebase_config.credentials,
        "Certificate",
        lambda path: SimpleNamespace(project_id="eventsurfer-test"),
    )
    monkeypatch.setattr(
        firebase_config.firebase_admin,
        "initialize_app",
        lambda *args, **kwargs: pytest.fail(
            "Firebase app should not initialize with mismatched projects."
        ),
    )

    with pytest.raises(ValueError, match="does not match"):
        firebase_config.initialize_firebase_app()


def test_existing_firebase_app_is_reused(monkeypatch):
    app = SimpleNamespace(project_id="eventsurfer-prod")
    monkeypatch.setattr(
        firebase_config,
        "FirebaseSettings",
        lambda: SimpleNamespace(
            firebase_project_id="eventsurfer-prod",
            firebase_service_account_json=None,
        ),
    )
    monkeypatch.setattr(
        firebase_config.firebase_admin,
        "get_app",
        lambda: app,
    )
    monkeypatch.setattr(
        firebase_config.firebase_admin,
        "initialize_app",
        lambda *args, **kwargs: pytest.fail(
            "Initialized Firebase app should be reused."
        ),
    )

    assert firebase_config.initialize_firebase_app() is app
