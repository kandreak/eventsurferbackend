from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from pymongo import AsyncMongoClient

from events.api.routes.settings import router as settings_router
from events.core.settings import BackendSettings
from events.persistence.mongodb import MongoDatabase


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = BackendSettings()
    app.state.database = None
    if settings.mongodb_uri is not None:
        client = AsyncMongoClient(settings.mongodb_uri.get_secret_value())
        mongo = MongoDatabase(client, settings.mongodb_database)
        try:
            await mongo.initialize()
        except Exception:
            await mongo.close()
            raise
        app.state.mongo = mongo
        app.state.database = mongo.database
    try:
        yield
    finally:
        mongo = getattr(app.state, "mongo", None)
        if mongo is not None:
            await mongo.close()


app = FastAPI(
    title="Events Aggregator API",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(settings_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
