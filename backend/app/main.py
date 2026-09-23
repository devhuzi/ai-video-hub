"""FastAPI application factory: middleware, error handlers, startup recovery."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import database as db

from . import api, config
from .integrations import nextcloud, nocodb

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "Interrupted by server restart — click Retry to resume"


async def startup():
    if not config.APP_PASSWORD:
        logger.critical("APP_PASSWORD is empty or unset. Set it in the environment or .env — refusing to start.")
        raise RuntimeError("APP_PASSWORD must be set")
    db.set_data_dir(config.DATA_DIR)
    await db.get_db()
    recovered = await db.recover_interrupted(INTERRUPTED_MESSAGE)
    if recovered:
        logger.warning("Marked %d interrupted pipeline(s) as failed: %s", len(recovered), recovered)
    nocodb.log_status_once()
    nextcloud.log_status_once()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await startup()
    try:
        yield
    finally:
        await db.close_db()


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(parts) or "Invalid request"


def create_app() -> FastAPI:
    app = FastAPI(title="AI Video Production Hub", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": _validation_message(exc)})

    @app.exception_handler(Exception)
    async def _on_unhandled(_request: Request, exc: Exception):
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    app.include_router(api.public_router)
    app.include_router(api.router)

    if config.CORS_ORIGINS:
        # Auth uses a bearer header, not cookies, so credentialed CORS is never needed.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.CORS_ORIGINS,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
    return app


app = create_app()
