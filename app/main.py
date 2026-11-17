"""Application factory for the pricing exception service.

``create_app`` builds the database, the service and the routes, loads the
reference data and serves the API under ``/api`` and the web UI under
``/ui``. Nothing runs at import time, so every call returns an
independent application.

Start it with ``uvicorn app.main:create_app --factory``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import register_error_handlers, router
from app.config import Settings
from app.domain import Policy
from app.repository import SqliteDatabase
from app.seed import seed_demo_requests, seed_reference_data
from app.service import PricingExceptionService

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a ready-to-serve application.

    Args:
        settings: Configuration to use. Defaults to the environment.

    Returns:
        FastAPI application with the API under ``/api`` and the UI under
        ``/ui``.
    """
    settings = settings or Settings.from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    db = SqliteDatabase(settings.db_path)
    service = PricingExceptionService(
        db, policy=Policy(max_discount_bps=settings.max_discount_bps)
    )
    seed_reference_data(db)
    if settings.seed_demo_requests:
        seed_demo_requests(service)

    app = FastAPI(title="Pricing exception service", version="1.0.0")
    app.state.service = service
    register_error_handlers(app)
    app.include_router(router)
    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    @app.get("/", include_in_schema=False)
    def redirect_to_ui() -> RedirectResponse:
        """Send visitors to the UI.

        Returns:
            Redirect to ``/ui/``.
        """
        return RedirectResponse("/ui/")

    return app
