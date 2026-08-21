"""GovTrans API application factory.

Startup order matters: logging redaction first, then key validation
(fail-fast in production), then orchestrator + run recovery.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.config import get_settings
from apps.api.security import install_log_redaction, validate_required_keys
from services.orchestrator.engine import Orchestrator
from services.orchestrator.tofu_client import TofuClient

logger = logging.getLogger("govtrans.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    install_log_redaction(settings)
    validate_required_keys(settings)

    tofu = TofuClient(settings.tofu_base_url, timeout=settings.tofu_timeout_seconds)
    app.state.tofu = tofu
    app.state.orchestrator = Orchestrator(settings, tofu)
    resumed = app.state.orchestrator.resume_active_runs()
    if resumed:
        logger.info("resumed %d active runs after restart", resumed)
    yield
    await tofu.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="GovTrans API", version="0.1.0", lifespan=lifespan)
    from apps.api.routes import health, runs, terms

    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(terms.router)
    return app


app = create_app()
