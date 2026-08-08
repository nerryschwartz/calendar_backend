"""FastAPI application factory."""

from __future__ import annotations

from calendar_backend.api.routers import (
    constraints,
    deletion,
    free_time,
    notifications,
    plans,
    repetition,
    schedule,
    settings,
    timers,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(
        title="calendar_backend API",
        description="HTTP API for calendar_backend frontend integration (V3).",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    app.include_router(plans.router)
    app.include_router(schedule.router)
    app.include_router(timers.router)
    app.include_router(notifications.router)
    app.include_router(settings.router)
    app.include_router(constraints.router)
    app.include_router(free_time.router)
    app.include_router(repetition.router)
    app.include_router(deletion.router)
    return app


app = create_app()
