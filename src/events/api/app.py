from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import os
from pathlib import Path
import socket
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from starlette.exceptions import HTTPException as StarletteHTTPException

from events.api.errors import (
    database_error_handler,
    http_exception_handler,
    persisted_validation_handler,
    request_validation_handler,
    unexpected_error_handler,
)
from events.api.routes.events import router as events_router
from events.api.routes.runs import router as runs_router
from events.api.routes.settings import router as settings_router
from events.config import load_settings
from events.core.settings import BackendSettings
from events.dispatcher import RunDispatcher
from events.executor import RunExecutor
from events.persistence.events_repository import EventsRepository
from events.persistence.mongodb import MongoDatabase
from events.persistence.posts_repository import PostsRepository
from events.persistence.run_posts_repository import RunPostsRepository
from events.persistence.runs_repository import RunsRepository
from events.secrets import Secrets


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    backend_settings = BackendSettings()
    app_settings = load_settings(Path("config/settings.json"))
    app.state.run_execution_settings = app_settings.run_execution
    app.state.database = None
    app.state.run_dispatcher = None
    mongo = None
    if backend_settings.mongodb_uri is not None:
        client = AsyncMongoClient(
            backend_settings.mongodb_uri.get_secret_value()
        )
        mongo = MongoDatabase(client, backend_settings.mongodb_database)
        try:
            await mongo.initialize()
        except Exception:
            await mongo.close()
            raise
        app.state.mongo = mongo
        app.state.database = mongo.database
        database = mongo.database
        executor = RunExecutor(
            runs_repository=RunsRepository(
                database["runs"],
                app_settings.run_execution,
            ),
            run_posts_repository=RunPostsRepository(
                database["run_posts"]
            ),
            posts_repository=PostsRepository(database["posts"]),
            events_repository=EventsRepository(database["events"]),
            settings=app_settings,
            secrets=Secrets(),
            worker_id=f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}",
        )
        dispatcher = RunDispatcher(
            executor,
            max_concurrent_runs=(
                app_settings.run_execution.max_concurrent_runs
            ),
            poll_interval_seconds=(
                app_settings.run_execution.dispatch_poll_interval_seconds
            ),
        )
        app.state.run_dispatcher = dispatcher
        dispatcher.start()
    try:
        yield
    finally:
        dispatcher = getattr(app.state, "run_dispatcher", None)
        if dispatcher is not None:
            await dispatcher.stop()
        if mongo is not None:
            await mongo.close()


app = FastAPI(
    title="Events Aggregator API",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, request_validation_handler)
app.add_exception_handler(ValidationError, persisted_validation_handler)
app.add_exception_handler(PyMongoError, database_error_handler)
app.add_exception_handler(Exception, unexpected_error_handler)
app.include_router(settings_router)
app.include_router(events_router)
app.include_router(runs_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
